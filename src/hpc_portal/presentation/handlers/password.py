"""本人用パスワード変更画面とAPIを提供する。"""

from jupyterhub.handlers.base import BaseHandler
from tornado import web

from hpc_portal.domain.errors import UseCaseError
from hpc_portal.entrypoints.dependencies import get_dependencies
from hpc_portal.presentation.schemas import (
    HpcPasswordChangeRequest,
    HpcRequestValidationError,
    parse_json_request,
)


class HpcPasswordPageHandler(BaseHandler):
    """ログイン中ユーザー本人のパスワード変更画面。"""

    @web.authenticated
    async def get(self):
        """本人用パスワード変更画面を表示する。"""
        self.set_header("Cache-Control", "no-store")
        xsrf_token = self.xsrf_token
        if isinstance(xsrf_token, bytes):
            xsrf_token = xsrf_token.decode("utf-8", errors="replace")
        html_out = await self.render_template(
            "account_password.html",
            xsrf_token=xsrf_token,
        )
        self.finish(html_out)


class HpcPasswordApiHandler(BaseHandler):
    """ログイン中ユーザー本人のパスワード変更API。"""

    def _api_error(self, status: int, message: str):
        """JSON形式のAPIエラーを返す。

        Args:
            status: HTTPステータスコード。
            message: 利用者へ返すエラーメッセージ。
        """
        self.set_status(status)
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        self.finish({"error": message})

    @web.authenticated
    async def post(self):
        """現在のパスワードを確認して本人のLinuxパスワードを変更する。"""
        self.set_header("Cache-Control", "no-store")
        username = self.current_user.name
        try:
            request = parse_json_request(
                self.request.body,
                HpcPasswordChangeRequest,
            )
        except HpcRequestValidationError as exc:
            return self._api_error(400, str(exc))
        try:
            result = await get_dependencies().users.change_password(
                username,
                request.current_password,
                request.new_password,
                request.confirm_password,
                str(getattr(self.authenticator, "service", "login") or "login"),
            )
        except UseCaseError as exc:
            return self._api_error(400, str(exc))
        self.write(result)
