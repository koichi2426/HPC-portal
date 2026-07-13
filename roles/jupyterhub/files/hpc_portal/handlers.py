"""HPCポータルの画面とJSON APIハンドラを登録する。"""

from jupyterhub.handlers.pages import SpawnHandler

from .apps import HpcAppDetailHandler, _is_openwebui_spawner
from .common import (
    BaseHandler,
    HPC_LITELLM_PUBLIC_BASE_URL,
    HPC_PORTAL_GRANT_SUDO,
    HPC_PORTAL_PROTECTED_USERS,
    c,
    json,
    logging,
    time,
    url_path_join,
    web,
)
from .litellm import (
    _hpc_litellm_admin_set_api_access,
    _hpc_litellm_delete_user_keys,
    _hpc_litellm_enabled,
    _hpc_litellm_generate_key,
    _hpc_litellm_list_models,
    _hpc_litellm_regenerate_own_key,
    _hpc_litellm_user_admin_disabled,
    _hpc_log_litellm_action,
    _hpc_safe_litellm_error,
)
from .ollama import _hpc_ollama_cmd, _hpc_ollama_pull_progress
from .resources import (
    HpcAppStatusJsHandler,
    HpcPortalCssHandler,
    HpcResourceMeterJsHandler,
    HpcResourceStatusHandler,
)
from .users import (
    _hpc_create_linux_user,
    _hpc_delete_linux_user,
    _hpc_generate_password,
    _hpc_is_portal_admin,
    _hpc_linux_users_snapshot,
    _hpc_run_cmd,
    _hpc_set_linux_password,
    _hpc_validate_password,
    _hpc_validate_username,
    _hpc_verify_linux_password,
)

HPC_PASSWORD_LOG = logging.getLogger("jupyterhub.hpc-password")


def _hpc_log_password_success(actor: str, target: str) -> None:
    """パスワードを含めず、変更・再発行の成功を監査ログへ記録する。"""
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    HPC_PASSWORD_LOG.info(
        "timestamp=%s actor=%s target=%s",
        timestamp,
        actor,
        target,
    )


class HpcAdminUsersPageHandler(BaseHandler):
    """Linux ユーザー管理 UI（/hub/admin/users）"""

    @web.authenticated
    async def get(self):
        """管理者向けLinuxユーザー管理画面を表示する。

        Raises:
            web.HTTPError: ポータル管理者ではない場合。
        """
        if not _hpc_is_portal_admin(self.current_user):
            raise web.HTTPError(403, "管理者のみアクセスできます")
        # JS 無効時の form GET 送信でパスワードが URL に載るのを防ぐ
        if self.get_argument("username", default=None) or self.get_argument(
            "password", default=None
        ):
            self.redirect(url_path_join(self.hub.base_url, "admin", "users"))
            return
        users = _hpc_linux_users_snapshot()
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
        self.set_status(status)
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        self.finish({"error": message})

    @web.authenticated
    async def post(self):
        """現在のパスワードを確認して本人のLinuxパスワードを変更する。"""
        self.set_header("Cache-Control", "no-store")
        username = self.current_user.name
        try:
            body = json.loads(self.request.body.decode("utf-8")) if self.request.body else {}
        except json.JSONDecodeError as exc:
            return self._api_error(400, f"Invalid JSON: {exc}")
        current_password = str(body.get("current_password", ""))
        new_password = str(body.get("new_password", ""))
        confirm_password = str(body.get("confirm_password", ""))
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


async def _hpc_stop_user_openwebui_servers(handler, username: str) -> str | None:
    """管理中spawnerと残存Slurm jobの両方からOpen WebUIを停止する。"""
    errors = []
    try:
        target_user = handler.find_user(username)
    except Exception as exc:
        target_user = None
        errors.append(_hpc_safe_litellm_error(exc))
    if target_user is not None:
        for server_name, spawner in list(target_user.spawners.items()):
            if not _is_openwebui_spawner(spawner):
                continue
            if not (getattr(spawner, "active", False) or getattr(spawner, "pending", None)):
                continue
            try:
                await target_user.stop(server_name)
            except Exception as exc:
                errors.append(_hpc_safe_litellm_error(exc))

    # Hub stateに残っていないjobも、Open WebUI専用job名で回収する。
    queue = _hpc_run_cmd(["squeue", "-h", "-u", username, "-n", "jhub-openwebui", "-o", "%A"])
    if queue.returncode == 0:
        job_ids = [line.strip() for line in queue.stdout.splitlines() if line.strip().isdigit()]
        if job_ids:
            canceled = _hpc_run_cmd(["scancel", *job_ids])
            if canceled.returncode != 0:
                errors.append((canceled.stderr or canceled.stdout or "scancel failed").strip())
    else:
        errors.append((queue.stderr or queue.stdout or "squeue failed").strip())

    if errors:
        joined = "; ".join(_hpc_safe_litellm_error(error) for error in errors)
        _hpc_log_litellm_action("openwebui_stop", username, "failed", joined)
        return joined
    _hpc_log_litellm_action("openwebui_stop", username, "ok")
    return None


class HpcAdminUsersApiHandler(BaseHandler):
    """Linux ユーザー管理 API"""

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

    def _require_admin(self):
        """操作ユーザーがポータル管理者であることを検証する。

        Raises:
            web.HTTPError: ポータル管理者ではない場合。
        """
        if not _hpc_is_portal_admin(self.current_user):
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
        self.write({"users": _hpc_linux_users_snapshot()})

    @web.authenticated
    async def post(self):
        """ユーザー・APIアクセス・shared Ollamaの管理操作を実行する。"""
        self._require_admin()
        self.set_header("Cache-Control", "no-store")
        body = self.get_json_body() or {}
        action = str(body.get("action", "")).strip().lower()
        username = str(body.get("username", "")).strip().lower()
        actor = self.current_user.name

        if action == "create":
            err = _hpc_validate_username(username)
            if err:
                return self._api_error(400, err)
            initial_password = _hpc_generate_password()
            grant_sudo = bool(body.get("sudo", HPC_PORTAL_GRANT_SUDO))
            err = _hpc_create_linux_user(username, initial_password, grant_sudo)
            if err:
                return self._api_error(400, err)
            api_key, key_warning = _hpc_litellm_generate_key(username)
            self.set_status(201)
            body = {
                "ok": True,
                "username": username,
                "initial_password": initial_password,
            }
            if api_key:
                body["api_key"] = api_key
                body["api_base_url"] = HPC_LITELLM_PUBLIC_BASE_URL
            if key_warning:
                body["warning"] = "API key 未発行: " + key_warning
            self.write(body)
            return

        if action == "delete":
            if not username:
                return self._api_error(400, "username が必要です")
            err = _hpc_delete_linux_user(username, actor)
            if err:
                return self._api_error(400, err)
            key_warning = _hpc_litellm_delete_user_keys(username)
            body = {"ok": True}
            if key_warning:
                body["warning"] = "LiteLLM key 無効化未確認: " + key_warning
            self.write(body)
            return

        if action == "password_regenerate":
            if not username:
                return self._api_error(400, "username が必要です")
            if username in HPC_PORTAL_PROTECTED_USERS:
                return self._api_error(400, "保護されたユーザーは再発行できません")
            password = _hpc_generate_password()
            err = _hpc_set_linux_password(username, password)
            if err:
                return self._api_error(400, err)
            _hpc_log_password_success(actor, username)
            self.write({"ok": True, "username": username, "initial_password": password})
            return

        if action in {"api_disable", "api_enable"}:
            if not username:
                return self._api_error(400, "username が必要です")
            enabled = action == "api_enable"
            err = _hpc_litellm_admin_set_api_access(username, enabled)
            stop_err = None
            if not enabled:
                # key blockが一部失敗しても、起動中Open WebUIの停止は必ず試行する。
                stop_err = await _hpc_stop_user_openwebui_servers(self, username)
            errors = [error for error in (err, stop_err) if error]
            if errors:
                return self._api_error(400, "; ".join(errors))
            self.write({"ok": True, "enabled": enabled})
            return

        if action in {"ollama_start", "ollama_stop", "ollama_status", "ollama_tags", "ollama_pull", "ollama_pull_status", "ollama_delete"}:
            mapping = {
                "ollama_start": "start",
                "ollama_stop": "stop",
                "ollama_status": "status",
                "ollama_tags": "tags",
                "ollama_pull": "pull",
                "ollama_pull_status": "pull-status",
                "ollama_delete": "delete",
            }
            model = str(body.get("model", "")).strip()
            if action == "ollama_pull_status":
                data, err = _hpc_ollama_pull_progress(model or None)
            else:
                data, err = _hpc_ollama_cmd(
                    mapping[action],
                    model if action in {"ollama_pull", "ollama_delete"} else None,
                    str(body.get("cpus", "")).strip() if action == "ollama_start" else None,
                    str(body.get("memory", "")).strip() if action == "ollama_start" else None,
                )
            if err:
                return self._api_error(400, err)
            self.write({"ok": True, "data": data})
            return

        self._api_error(400, "不明な action です")


class HpcAdminRedirectHandler(BaseHandler):
    """JupyterHub 標準 /hub/admin を Linux ユーザー管理へ転送する"""

    @web.authenticated
    async def get(self, *args, **kwargs):
        """標準管理画面から権限に応じたポータル画面へ転送する。

        Args:
            *args: JupyterHubから渡される未使用の位置引数。
            **kwargs: JupyterHubから渡される未使用のキーワード引数。
        """
        if _hpc_is_portal_admin(self.current_user):
            self.redirect(url_path_join(self.hub.base_url, "admin", "users"))
        else:
            self.redirect(url_path_join(self.hub.base_url, "home"))


class HpcSpawnHandler(SpawnHandler):
    """起動要求後の遷移先をポータルHomeへ変更する。"""

    def _get_pending_url(self, user, server_name):
        """標準Pending画面ではなく、状態監視機能を持つHomeを返す。

        Args:
            user: 起動対象のJupyterHubユーザー。
            server_name: 起動対象のnamed server名。

        Returns:
            ポータルHomeのURL。
        """
        return url_path_join(self.hub.base_url, "home")


import jupyterhub.handlers as _jh_handlers
import jupyterhub.handlers.pages as _jh_pages_handlers

for _handlers in (_jh_pages_handlers.default_handlers, _jh_handlers.default_handlers):
    for _idx, (_route, _handler_cls) in enumerate(_handlers):
        if _route == "/admin":
            _handlers[_idx] = (_route, HpcAdminRedirectHandler)
        elif _handler_cls is SpawnHandler:
            _handlers[_idx] = (_route, HpcSpawnHandler)


c.JupyterHub.extra_handlers.append((r"/hpc-resource-meter.js", HpcResourceMeterJsHandler))
c.JupyterHub.extra_handlers.append((r"/hpc-app-status.js", HpcAppStatusJsHandler))
c.JupyterHub.extra_handlers.append((r"/hpc-portal.css", HpcPortalCssHandler))
c.JupyterHub.extra_handlers.append((r"/hpc-resource-status", HpcResourceStatusHandler))
c.JupyterHub.extra_handlers.append((r"/apps/([^/]+)", HpcAppDetailHandler))
c.JupyterHub.extra_handlers.append((r"/llm-api/api", HpcLlmApiApiHandler))
c.JupyterHub.extra_handlers.append((r"/llm-api", HpcLlmApiPageHandler))
c.JupyterHub.extra_handlers.append((r"/account/password/api", HpcPasswordApiHandler))
c.JupyterHub.extra_handlers.append((r"/account/password", HpcPasswordPageHandler))
c.JupyterHub.extra_handlers.append((r"/admin/users/api", HpcAdminUsersApiHandler))
c.JupyterHub.extra_handlers.append((r"/admin/users", HpcAdminUsersPageHandler))
