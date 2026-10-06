"""ログイン済みブラウザから、個人別トークン・待受候補・API公開設定を操作する。"""

import json
from urllib.parse import quote

from jupyterhub.handlers.base import BaseHandler
from pydantic import ValidationError
from tornado import web

from hpc_portal.bootstrap.container import get_external_api
from hpc_portal.infrastructure.config.external_api_settings import ExternalApiSettings
from hpc_portal.presentation.api_presenter import (
    ApiPublicationPresenter,
    public_candidate,
)
from hpc_portal.presentation.schemas.requests.external_api import (
    Operation,
    Registration,
)


class BrowserHandler(BaseHandler):
    """トークン認証からの設定変更を拒否し、本人のログインセッションを要求する。"""

    async def prepare(self):
        """トークン認証による設定操作を拒否し、秘密値のキャッシュを防ぐ。

        Raises:
            web.HTTPError: トークン認証で設定画面へアクセスした場合。
        """
        await super().prepare()
        if self.get_auth_token() or getattr(self, "_token_authenticated", False):
            raise web.HTTPError(403, "ブラウザのログインセッションが必要です")

        self.set_header("Cache-Control", "no-store")
        self.set_header("Referrer-Policy", "no-referrer")

    def write_error(self, status_code, **kwargs):
        """内部のエラー内容を出さず、設定操作の失敗をJSONで返す。

        Args:
            status_code: HTTPステータスコード。
            **kwargs: Tornadoが渡す例外情報など。内部情報を応答へ出さず、ここでは参照しない。
        """
        self.set_header("Cache-Control", "no-store")
        self.finish(
            {"error": "操作できません。入力・接続先の状態・管理設定を確認してください"}
        )

    def api_usecases(self):
        """外部APIの共有操作を取得し、無効・設定不備をHTTPエラーへ変換する。

        Returns:
            外部APIの共有usecase。

        Raises:
            web.HTTPError: 外部APIが無効、または管理設定が不正な場合。
        """
        try:
            usecase = get_external_api()
            if usecase is None:
                raise ValueError("自作API公開は管理者が有効化していません")
            return usecase
        except ValueError as exc:
            raise web.HTTPError(503, str(exc)) from None


class ExternalApiPage(BrowserHandler):
    @web.authenticated
    async def get(self):
        """旧トークン画面のリンクから、統一画面のトークン欄へ移動する。"""
        self.redirect("/hub/api-publications#api-tokens")


class ExternalApiCredentials(BrowserHandler):
    @web.authenticated
    async def post(self):
        """本人の接続情報を表示・ダウンロード・サービス別に再発行する。"""
        try:
            op = Operation.model_validate_json(self.request.body)
            usecase = self.api_usecases()
            if op.action in {"reveal", "download"}:
                record = usecase.queries.credential_record(self.current_user)
            elif op.action in {"rotate_cloudflare", "rotate_jupyterhub"}:
                record = await usecase.rotate_credentials.execute(
                    self.current_user, op.action.removeprefix("rotate_")
                )
            else:
                raise ValueError("操作が不正です")
            if not record.get("enabled") or record.get("state") != "ready":
                raise ValueError("接続情報は発行待ちまたは利用停止中です")
            payload = {
                "version": 1,
                "username": self.current_user.name,
                "client_id": record["client_id"],
                "client_secret": record["client_secret"],
                "jupyterhub_token": record["hub_token"],
                "expires": "無期限",
                "updated_at": record.get("updated_at"),
                "base_url": f"https://{usecase.config.public_host}",
                "apis": {
                    r["name"]: ApiPublicationPresenter(
                        usecase.config, usecase.accounts
                    ).publication_url(r)
                    for r in usecase.queries.list_publications(self.current_user)
                },
            }
        except (ValueError, ValidationError):
            raise web.HTTPError(400) from None
        except Exception:
            raise web.HTTPError(503) from None
        if op.action == "download":
            self.set_header(
                "Content-Disposition", 'attachment; filename="hpc-api.json"'
            )
        self.finish(payload)


class ApiPublicationsPage(BrowserHandler):
    @web.authenticated
    async def get(self):
        """秘密値を画面へ渡さず、トークンの利用状態とAPI管理画面を表示する。"""
        config = ExternalApiSettings.from_env()
        available, state = False, "未設定"
        if config.enabled:
            try:
                record = self.api_usecases().queries.credential_record(
                    self.current_user
                )
                state_key = record.get("state", "issuing")
                available = bool(record.get("enabled")) and state_key == "ready"
                state = {
                    "ready": "利用可能" if available else "利用停止中",
                    "issuing": "発行準備中",
                    "disabled": "利用停止中",
                    "revoking": "停止処理中",
                    "rotating_cloudflare": "Cloudflare更新中",
                    "rotating_jupyterhub": "JupyterHub更新中",
                }.get(state_key, "確認が必要")
            except (ValueError, web.HTTPError):
                state = "準備中"

        # URLのプレビューに必要な公開情報だけを渡し、トークンは操作時に取得する。
        public_url_prefix = (
            f"https://{config.public_host}/hub/user-api/"
            f"{quote(self.current_user.name, safe='')}/"
        )
        self.finish(
            await self.render_template(
                "api_publications.html",
                configured=config.enabled,
                api_available=available,
                credential_state=state,
                public_url_prefix=public_url_prefix,
            )
        )


class ApiPorts(BrowserHandler):
    @web.authenticated
    async def get(self):
        """本人の待受候補と空きポートを取得し、画面向けの情報をJSONで返す。"""
        usecase = self.api_usecases()
        try:
            data = await usecase.list_ports.execute(self.current_user)
        except (OSError, ValueError):
            raise web.HTTPError(503) from None
        home = usecase.accounts.getpwnam(self.current_user.name).pw_dir
        data["listeners"] = [public_candidate(row, home) for row in data["listeners"]]
        self.finish(data)


class ApiPublications(BrowserHandler):
    @web.authenticated
    async def get(self):
        """外部設定を変更せず、現在のAPI公開登録をJSONで返す。"""
        usecase = self.api_usecases()
        # ページ更新で外部設定を書き換えないよう、再同期は定期処理に任せる。
        rows = usecase.queries.list_publications(self.current_user)
        self.finish(
            {
                "apps": [
                    ApiPublicationPresenter(
                        usecase.config, usecase.accounts
                    ).publication_info(row)
                    for row in rows
                ]
            }
        )

    @web.authenticated
    async def post(self):
        """JSON入力を検証し、API登録・再公開・公開停止・削除を実行する。"""
        try:
            usecase = self.api_usecases()
            data = json.loads(self.request.body)
            if not isinstance(data, dict):
                raise ValueError("JSON オブジェクトが必要です")
            if "action" in data:
                op = Operation.model_validate(data)
                if op.action not in {"publish", "unpublish", "delete"} or not op.name:
                    raise ValueError("操作が不正です")
                app = await usecase.operate_publication.execute(
                    self.current_user, op.name, op.action
                )
            else:
                app = await usecase.publish_api.execute(
                    self.current_user, Registration.model_validate(data)
                )
        except (ValueError, ValidationError):
            raise web.HTTPError(400) from None
        except Exception:
            raise web.HTTPError(503) from None
        self.finish(
            {
                "ok": True,
                "app": ApiPublicationPresenter(
                    usecase.config, usecase.accounts
                ).publication_info(app),
            }
        )
