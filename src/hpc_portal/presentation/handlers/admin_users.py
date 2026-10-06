"""管理者向けユーザー・LLM・Ollama画面とHTTP入力。"""

from jupyterhub.handlers.base import BaseHandler
from jupyterhub.utils import url_path_join
from tornado import web

from hpc_portal.bootstrap.container import get_container
from hpc_portal.domain.errors import UseCaseError
from hpc_portal.infrastructure.config.settings import HPC_PORTAL_GRANT_SUDO
from hpc_portal.infrastructure.linux.user_account_gateway import is_portal_admin
from hpc_portal.presentation.schemas.request_validation import (
    HpcRequestValidationError,
    parse_json_request,
)
from hpc_portal.presentation.schemas.requests.admin_users import HpcAdminUsersRequest
from hpc_portal.presentation.storage_formatter import format_storage_bytes


async def admin_users_snapshot():
    rows = await get_container().users.snapshot.execute()
    for row in rows:
        used = row["storage_used_bytes"]
        row["storage_used_label"] = (
            format_storage_bytes(used) if used is not None else "確認不可"
        )
    return rows


class HpcAdminUsersPageHandler(BaseHandler):
    """Linux ユーザー管理 UI（/hub/admin/users）"""

    @web.authenticated
    async def get(self):
        """管理者向けLinuxユーザー管理画面を表示する。

        Raises:
            web.HTTPError: ポータル管理者ではない場合。
        """
        if not is_portal_admin(self.current_user):
            raise web.HTTPError(403, "管理者のみアクセスできます")
        # JS 無効時の form GET 送信でパスワードが URL に載るのを防ぐ
        if self.get_argument("username", default=None) or self.get_argument(
            "password", default=None
        ):
            self.redirect(url_path_join(self.hub.base_url, "admin", "users"))
            return
        users = await admin_users_snapshot()
        xsrf_token = self.xsrf_token
        if isinstance(xsrf_token, bytes):
            xsrf_token = xsrf_token.decode("utf-8", errors="replace")
        html_out = await self.render_template(
            "admin_users.html",
            users=users,
            grant_sudo_default=HPC_PORTAL_GRANT_SUDO,
            xsrf_token=xsrf_token,
        )
        self.finish(html_out)


class HpcAdminUsersApiHandler(BaseHandler):
    """Linux ユーザー管理 API"""

    def _require_admin(self):
        """操作ユーザーがポータル管理者であることを検証する。

        Raises:
            web.HTTPError: ポータル管理者ではない場合。
        """
        if not is_portal_admin(self.current_user):
            raise web.HTTPError(403, "管理者のみ操作できます")

    def _api_error(self, status: int, message: str):
        """APIエラーをJSONで返す。

        Args:
            status: HTTPステータスコード。
            message: 利用者へ返すエラーメッセージ。
        """
        self.set_status(status)
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        self.finish({"error": message})

    @web.authenticated
    async def get(self):
        """Linuxユーザー一覧をJSONで返す。"""
        self._require_admin()
        self.set_header("Cache-Control", "no-store")
        self.write({"users": await admin_users_snapshot()})

    @web.authenticated
    async def post(self):
        self._require_admin()
        self.set_header("Cache-Control", "no-store")
        try:
            request = parse_json_request(self.request.body, HpcAdminUsersRequest)
            dependencies = get_container()
            if request.action.startswith("ollama_"):
                operations = {
                    "ollama_register_model": dependencies.ollama.ollama_register_model,
                    "ollama_sync_models": dependencies.ollama.ollama_sync_models,
                    "ollama_delete": dependencies.ollama.ollama_delete,
                    "ollama_start": dependencies.ollama.ollama_start,
                    "ollama_stop": dependencies.ollama.ollama_stop,
                    "ollama_update_check": dependencies.ollama.ollama_update_check,
                    "ollama_update": dependencies.ollama.ollama_update,
                    "ollama_status": dependencies.ollama.ollama_status,
                    "ollama_tags": dependencies.ollama.ollama_tags,
                    "ollama_pull": dependencies.ollama.ollama_pull,
                    "ollama_pull_cancel": dependencies.ollama.ollama_pull_cancel,
                    "ollama_pull_status": dependencies.ollama.ollama_pull_status,
                }
                operation = operations.get(request.action)
                if operation is None:
                    raise UseCaseError("不明な action です")
                result = await operation.execute(request)
            else:
                operations = {
                    "create": dependencies.users.create,
                    "display_name": dependencies.users.display_name,
                    "delete": dependencies.users.delete,
                    "password_regenerate": dependencies.users.password_regenerate,
                    "sudo_enable": dependencies.users.sudo,
                    "sudo_disable": dependencies.users.sudo,
                    "external_api_enable": dependencies.users.external_api,
                    "external_api_disable": dependencies.users.external_api,
                    "api_enable": dependencies.users.api,
                    "api_disable": dependencies.users.api,
                }
                operation = operations.get(request.action)
                if operation is None:
                    raise UseCaseError("不明な action です")
                result = await operation.execute(self.current_user.name, request)
        except HpcRequestValidationError as exc:
            return self._api_error(400, str(exc))
        except UseCaseError as exc:
            return self._api_error(503 if exc.code == "unavailable" else 400, str(exc))
        self.set_status(201 if request.action == "create" else 200)
        self.write(result)
