"""Browser-only credentials, live port inventory, and application publication."""

import json

from jupyterhub.handlers.base import BaseHandler
from pydantic import ValidationError
from tornado import web

from hpc_portal.bootstrap.container import get_external_api
from hpc_portal.infrastructure.config.external_api_settings import ExternalApiSettings
from hpc_portal.presentation.api_presenter import (
    ApiPublicationPresenter,
    public_candidate,
)
from hpc_portal.presentation.schemas.external_api import Operation, Registration


class BrowserHandler(BaseHandler):
    async def prepare(self):
        await super().prepare()
        if self.get_auth_token() or getattr(self, "_token_authenticated", False):
            raise web.HTTPError(403, "ブラウザのログインセッションが必要です")
        self.set_header("Cache-Control", "no-store")
        self.set_header("Referrer-Policy", "no-referrer")

    def write_error(self, status_code, **kwargs):
        self.set_header("Cache-Control", "no-store")
        self.finish(
            {"error": "操作できません。入力・接続先の状態・管理設定を確認してください"}
        )

    def services(self):
        try:
            usecase = get_external_api()
            if usecase is None:
                raise ValueError("外部 API 公開は管理者が有効化していません")
            return usecase
        except ValueError as exc:
            raise web.HTTPError(503, str(exc)) from None


class ExternalApiPage(BrowserHandler):
    @web.authenticated
    async def get(self):
        configured, state = ExternalApiSettings.from_env().enabled, "未設定"
        if configured:
            try:
                usecase = self.services()
                state_key = usecase.queries.credential_record(self.current_user).get(
                    "state", "issuing"
                )
                state = {
                    "ready": "利用可能",
                    "issuing": "発行準備中",
                    "disabled": "利用停止中",
                    "revoking": "利用停止中・失効処理待ち",
                    "rotating_cloudflare": "Cloudflare トークン更新中",
                    "rotating_jupyterhub": "JupyterHub トークン更新中",
                }.get(state_key, "確認が必要です")
            except (ValueError, web.HTTPError):
                state = "発行待ち／管理設定を確認してください"
        self.finish(
            await self.render_template(
                "external_api.html", state=state, configured=configured
            )
        )


class ExternalApiCredentials(BrowserHandler):
    @web.authenticated
    async def post(self):
        try:
            op = Operation.model_validate_json(self.request.body)
            usecase = self.services()
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
        self.finish(
            await self.render_template(
                "api_publications.html",
                configured=ExternalApiSettings.from_env().enabled,
            )
        )


class ApiPorts(BrowserHandler):
    @web.authenticated
    async def get(self):
        usecase = self.services()
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
        usecase = self.services()
        # Reconciliation is background work; page refresh never invokes remote mutations.
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
        try:
            usecase = self.services()
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
