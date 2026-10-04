"""本人用LLM API管理画面とAPIを提供する。"""

from jupyterhub.handlers.base import BaseHandler
from tornado import web

from hpc_portal.entrypoints.dependencies import get_dependencies
from hpc_portal.presentation.schemas import (
    HpcLlmApiRequest,
    HpcRequestValidationError,
    parse_json_request,
)


class HpcLlmApiPageHandler(BaseHandler):
    """本人用 LLM API 管理 UI を表示する handler。

    ログイン中ユーザーの API key 状態、利用可能 model、API 利用例を
    `/hub/llm-api` に表示する。API key の生値はここでは取得しない。
    """

    @web.authenticated
    async def get(self):
        """ログイン中ユーザーのLLM API管理画面を表示する。"""
        xsrf_token = self.xsrf_token
        if isinstance(xsrf_token, bytes):
            xsrf_token = xsrf_token.decode("utf-8", errors="replace")
        disabled = False
        status_error = ""
        if get_dependencies().llm.client.enabled():
            disabled, err = get_dependencies().llm.gateway.user_admin_disabled(
                self.current_user.name
            )
            status_error = err or ""
        else:
            status_error = "LiteLLM Admin API が未設定です"
        models, models_error = get_dependencies().llm.list_models.execute()
        default_model = models[0]["id"] if models else ""
        html_out = await self.render_template(
            "llm_api.html",
            xsrf_token=xsrf_token,
            api_disabled=disabled,
            status_error=status_error,
            models=models,
            models_error=models_error,
            default_model=default_model,
        )
        self.finish(html_out)


class HpcLlmApiApiHandler(BaseHandler):
    """本人用 LiteLLM API key 操作 API。

    ログイン中ユーザー本人の key 再発行だけを受け付ける。
    管理者が無効化したユーザーは、下位の LiteLLM key 管理関数で拒否される。
    """

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
    async def post(self):
        """ログイン中ユーザー本人のLiteLLM APIキーを再発行する。"""
        try:
            parse_json_request(self.request.body, HpcLlmApiRequest)
        except HpcRequestValidationError as exc:
            return self._api_error(400, str(exc))
        api_key, err = get_dependencies().llm.regenerate_own_key.execute(
            self.current_user.name
        )
        if err:
            return self._api_error(400, err)
        self.write(
            {
                "ok": True,
                "api_key": api_key,
                "api_base_url": get_dependencies().users.settings.llm_public_base_url,
            }
        )
