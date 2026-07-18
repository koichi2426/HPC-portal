"""本人用パスワード変更画面とAPIを提供する。"""

import logging
import time

from tornado import web

from ..common import BaseHandler
from ..schemas import (
    HpcPasswordChangeRequest,
    HpcRequestValidationError,
    parse_json_request,
)
from ..users import (
    _hpc_set_linux_password,
    _hpc_validate_password,
    _hpc_verify_linux_password,
)

HPC_PASSWORD_LOG = logging.getLogger("jupyterhub.hpc-password")

def _hpc_log_password_success(actor: str, target: str) -> None:
    """パスワードを含めず、変更・再発行の成功を監査ログへ記録する。

    Args:
        actor: 操作を実行したユーザー名。
        target: 操作対象のユーザー名。
    """
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    HPC_PASSWORD_LOG.info(
        "timestamp=%s actor=%s target=%s",
        timestamp,
        actor,
        target,
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
        current_password = request.current_password
        new_password = request.new_password
        confirm_password = request.confirm_password
        if new_password != confirm_password:
            return self._api_error(400, "新しいパスワードが確認入力と一致しません")
        err = _hpc_validate_password(new_password)
        if err:
            return self._api_error(400, err)
        pam_service = str(getattr(self.authenticator, "service", "login") or "login")
        err = _hpc_verify_linux_password(
            username,
            current_password,
            service=pam_service,
        )
        if err:
            return self._api_error(400, err)
        err = _hpc_set_linux_password(username, new_password)
        if err:
            return self._api_error(400, err)
        _hpc_log_password_success(username, username)
        self.write({"ok": True})
