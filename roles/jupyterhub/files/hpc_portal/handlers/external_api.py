"""Browser-only credentials, live port inventory, and application publication."""
import asyncio
import json
import pwd

from pydantic import ValidationError
from tornado import web

from ..common import BaseHandler
from ..external_api.config import Config
from ..external_api.schema import Registration, Operation
from ..external_api.service import service


class BrowserHandler(BaseHandler):
    async def prepare(self):
        await super().prepare()
        if self.get_auth_token() or getattr(self, "_token_authenticated", False):
            raise web.HTTPError(403, "ブラウザのログインセッションが必要です")
        self.set_header("Cache-Control", "no-store")
        self.set_header("Referrer-Policy", "no-referrer")

    def write_error(self, status_code, **kwargs):
        self.set_header("Cache-Control", "no-store")
        self.finish({"error": "操作できません。入力・接続先の状態・管理設定を確認してください"})

    def services(self):
        try:
            return service()
        except ValueError as exc:
            raise web.HTTPError(503, str(exc)) from None


class ExternalApiPage(BrowserHandler):
    @web.authenticated
    async def get(self):
        configured, state = Config.from_env().enabled, "未設定"
        if configured:
            try:
                credentials, _ = self.services()
                state_key = credentials.record(self.current_user).get("state", "issuing")
                state = {"ready": "利用可能", "issuing": "発行準備中", "disabled": "利用停止中",
                    "revoking": "利用停止中・失効処理待ち", "rotating_cloudflare": "Cloudflare トークン更新中",
                    "rotating_jupyterhub": "JupyterHub トークン更新中"}.get(state_key, "確認が必要です")
            except (ValueError, web.HTTPError):
                state = "発行待ち／管理設定を確認してください"
        self.finish(await self.render_template("external_api.html", state=state, configured=configured))


class ExternalApiCredentials(BrowserHandler):
    @web.authenticated
    async def post(self):
        try:
            op = Operation.model_validate_json(self.request.body)
            credentials, publications = self.services()
            if op.action in {"reveal", "download"}:
                record = credentials.record(self.current_user)
            elif op.action in {"rotate_cloudflare", "rotate_jupyterhub"}:
                record = await credentials.rotate(self.current_user, op.action.removeprefix("rotate_"))
            else:
                raise ValueError("操作が不正です")
            if not record.get("enabled") or record.get("state") != "ready":
                raise ValueError("接続情報は発行待ちまたは利用停止中です")
            payload = {"version": 1, "username": self.current_user.name,
                "client_id": record["client_id"], "client_secret": record["client_secret"],
                "jupyterhub_token": record["hub_token"], "expires": "無期限", "updated_at": record.get("updated_at"),
                "base_url": f"https://{credentials.config.public_host}",
                "apis": {r["name"]: publications.url(r) for r in publications.list(self.current_user)}}
        except (ValueError, ValidationError):
            raise web.HTTPError(400) from None
        except Exception:
            raise web.HTTPError(503) from None
        if op.action == "download":
            self.set_header("Content-Disposition", 'attachment; filename="hpc-api.json"')
        self.finish(payload)


class ApiPublicationsPage(BrowserHandler):
    @web.authenticated
    async def get(self):
        self.finish(await self.render_template("api_publications.html", configured=Config.from_env().enabled))


class ApiPorts(BrowserHandler):
    @web.authenticated
    async def get(self):
        _credentials, publications = self.services()
        entry = pwd.getpwnam(self.current_user.name)
        try:
            data = await asyncio.to_thread(publications.inventory.ports, entry.pw_uid, entry.pw_dir)
        except (OSError, ValueError):
            raise web.HTTPError(503) from None
        self.finish(data)


class ApiPublications(BrowserHandler):
    @web.authenticated
    async def get(self):
        _credentials, publications = self.services()
        # Reconciliation is background work; page refresh never invokes remote mutations.
        rows = publications.list(self.current_user)
        self.finish({"apps": [publications.public(row) for row in rows]})

    @web.authenticated
    async def post(self):
        try:
            _credentials, publications = self.services()
            data = json.loads(self.request.body)
            if not isinstance(data, dict):
                raise ValueError("JSON オブジェクトが必要です")
            if "action" in data:
                op = Operation.model_validate(data)
                if op.action not in {"publish", "unpublish", "delete"} or not op.name:
                    raise ValueError("操作が不正です")
                app = await publications.operate(self.current_user, op.name, op.action)
            else:
                app = await publications.register(self.current_user, Registration.model_validate(data))
        except (ValueError, ValidationError):
            raise web.HTTPError(400) from None
        except Exception:
            raise web.HTTPError(503) from None
        self.finish({"ok": True, "app": publications.public(app)})
