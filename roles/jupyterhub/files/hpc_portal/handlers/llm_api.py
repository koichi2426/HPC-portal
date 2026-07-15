"""本人用LLM API管理画面とAPIを提供する。"""

import json

from tornado import web

from ..common import BaseHandler, HPC_LITELLM_PUBLIC_BASE_URL
from ..litellm import (
    _hpc_litellm_enabled,
    _hpc_litellm_list_models,
    _hpc_litellm_regenerate_own_key,
    _hpc_litellm_user_admin_disabled,
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
        if _hpc_litellm_enabled():
            disabled, err = _hpc_litellm_user_admin_disabled(self.current_user.name)
            status_error = err or ""
        else:
            status_error = "LiteLLM Admin API が未設定です"
        models, models_error = _hpc_litellm_list_models()
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

    def get_json_body(self):
        """リクエスト本文をJSONオブジェクトとして取得する。

        Returns:
            JSON本文。本文が空の場合は空の辞書。

        Raises:
            web.HTTPError: JSONとして解釈できない場合。
        """
        if not self.request.body:
            return {}
        try:
            return json.loads(self.request.body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise web.HTTPError(400, f"Invalid JSON: {exc}") from exc

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
        body = self.get_json_body()
        action = str(body.get("action", "")).strip().lower()
        if action != "regenerate":
            return self._api_error(400, "不明な action です")
        api_key, err = _hpc_litellm_regenerate_own_key(self.current_user.name)
        if err:
            return self._api_error(400, err)
        self.write({
            "ok": True,
            "api_key": api_key,
            "api_base_url": HPC_LITELLM_PUBLIC_BASE_URL,
        })
