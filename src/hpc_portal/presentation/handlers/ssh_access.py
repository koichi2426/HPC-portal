"""ログイン済みの本人へSSHトークンと接続設定を提供する。"""

from pydantic import ValidationError
from tornado import web

from hpc_portal.bootstrap.container import get_ssh_access
from hpc_portal.presentation.handlers.external_api import BrowserHandler
from hpc_portal.presentation.schemas.requests.ssh_access import SshOperation


class SshAccessPage(BrowserHandler):
    @web.authenticated
    async def get(self):
        """秘密値をHTMLへ埋め込まず、SSH接続の操作画面を表示する。"""
        usecase = get_ssh_access()
        self.finish(
            await self.render_template(
                "ssh_access.html", configured=usecase is not None
            )
        )


class SshCredentials(BrowserHandler):
    @web.authenticated
    async def post(self):
        """本人のトークン操作を実行し、ホスト名・ユーザー名と接続情報を返す。"""
        try:
            usecase = get_ssh_access()
            if usecase is None:
                raise ValueError("SSH公開は未設定です")
            op = SshOperation.model_validate_json(self.request.body)
            if op.action in {"reveal", "download"}:
                record = usecase.record(self.current_user)
            else:
                record = await usecase.execute(self.current_user, op.action)
            ready = record.get("enabled") and record["state"] == "ready"
            if op.action == "download" and not ready:
                raise ValueError("SSHの接続情報は発行されていません")
            payload = {
                "version": 1,
                "purpose": "ssh",
                "hostname": usecase.config.public_host,
                "username": self.current_user.name,
                "port": 22,
                "enabled": record.get("enabled", True),
                "state": record["state"],
                "client_id": record.get("client_id", "") if ready else "",
                "client_secret": record.get("client_secret", "") if ready else "",
                "updated_at": record.get("updated_at"),
            }
        except (ValueError, ValidationError):
            raise web.HTTPError(400) from None
        except Exception:
            raise web.HTTPError(503) from None
        if op.action == "download":
            self.set_header(
                "Content-Disposition", 'attachment; filename="hpc-ssh.json"'
            )
        self.finish(payload)
