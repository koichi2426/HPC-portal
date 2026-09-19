"""管理者向けLinuxユーザー・LLM API・Ollama管理を提供する。"""

import asyncio
import logging
import time

from jupyterhub.utils import url_path_join
from tornado import web

from ..apps import _is_openwebui_spawner
from ..common import (
    BaseHandler,
    HPC_LITELLM_PUBLIC_BASE_URL,
    HPC_PORTAL_GRANT_SUDO,
    HPC_PORTAL_PROTECTED_USERS,
)
from ..litellm import (
    _hpc_litellm_admin_set_api_access,
    _hpc_litellm_delete_ollama_model,
    _hpc_litellm_delete_user_keys,
    _hpc_litellm_enabled,
    _hpc_litellm_generate_key,
    _hpc_litellm_register_ollama_model,
    _hpc_litellm_sync_ollama_models,
    _hpc_litellm_user_external_api_state,
    _hpc_log_litellm_action,
    _hpc_safe_litellm_error,
)
from ..ollama import _hpc_ollama_cmd, _hpc_ollama_has_model, _hpc_ollama_pull_progress
from ..schemas import (
    HpcAdminUsersRequest,
    HpcRequestValidationError,
    parse_json_request,
)
from ..users import (
    _hpc_create_linux_user,
    _hpc_delete_linux_user,
    _hpc_generate_password,
    _hpc_home_storage_usage,
    _hpc_is_portal_admin,
    _hpc_linux_users_snapshot,
    _hpc_run_cmd,
    _hpc_set_linux_display_name,
    _hpc_set_linux_password,
    _hpc_set_linux_sudo,
    _hpc_validate_display_name,
    _hpc_validate_username,
)
from .password import _hpc_log_password_success
from .utils import _hpc_format_storage_bytes

HPC_USER_ADMIN_LOG = logging.getLogger("jupyterhub.hpc-user-admin")
HPC_OLLAMA_LOG = logging.getLogger("jupyterhub.hpc-ollama")

_HPC_ADMIN_API_STATUS_CONCURRENCY = 8
_HPC_ADMIN_STORAGE_CONCURRENCY = 4
_HPC_OLLAMA_REGISTRATION_TASKS: dict[str, asyncio.Task] = {}

async def _hpc_watch_ollama_pull_and_register(model: str) -> None:
    """Ollama pull完了を監視してLiteLLMへ登録する。

    Args:
        model: pullを開始したOllamaモデル名。
    """
    idle_count = 0
    try:
        while True:
            progress, err = await asyncio.to_thread(_hpc_ollama_pull_progress, model)
            if err:
                HPC_OLLAMA_LOG.warning(
                    "action=model_register model=%s result=failed error=%s",
                    model,
                    _hpc_safe_litellm_error(err),
                )
                return
            state = str((progress or {}).get("state") or "")
            if state == "completed":
                _registration, registration_err = await asyncio.to_thread(
                    _hpc_litellm_register_ollama_model, model
                )
                if registration_err:
                    HPC_OLLAMA_LOG.warning(
                        "action=model_register model=%s result=failed error=%s",
                        model,
                        _hpc_safe_litellm_error(registration_err),
                    )
                return
            if state in {"failed", "busy", "cancelled", "cancelled_cleanup_failed"}:
                return
            idle_count = idle_count + 1 if state == "idle" else 0
            if idle_count >= 20:
                HPC_OLLAMA_LOG.warning(
                    "action=model_register model=%s result=failed error=pull_status_timeout",
                    model,
                )
                return
            await asyncio.sleep(1.5)
    except Exception as exc:  # noqa: BLE001
        HPC_OLLAMA_LOG.warning(
            "action=model_register model=%s result=failed error=%s",
            model,
            _hpc_safe_litellm_error(exc),
        )
    finally:
        current = asyncio.current_task()
        if _HPC_OLLAMA_REGISTRATION_TASKS.get(model) is current:
            _HPC_OLLAMA_REGISTRATION_TASKS.pop(model, None)

def _hpc_start_ollama_registration_watcher(model: str) -> None:
    """モデル登録監視タスクを重複なく開始する。

    Args:
        model: pullを開始したOllamaモデル名。
    """
    existing = _HPC_OLLAMA_REGISTRATION_TASKS.get(model)
    if existing and not existing.done():
        return
    _HPC_OLLAMA_REGISTRATION_TASKS[model] = asyncio.create_task(
        _hpc_watch_ollama_pull_and_register(model)
    )

def _hpc_litellm_access_state(username: str) -> tuple[str, str]:
    """管理者によるLLM API利用状態を一覧表示用に取得する。

    Args:
        username: 状態を取得するLinuxユーザー名。

    Returns:
        状態と画面表示用エラーメッセージの組。
    """
    state, err = _hpc_litellm_user_external_api_state(username)
    return state, ("LLM APIの状態を取得できません" if err else "")

async def _hpc_admin_users_snapshot() -> list[dict]:
    """Linuxユーザー一覧へLLM API状態とホーム使用量を付加する。

    Returns:
        LLM API状態とストレージ使用量を含むユーザー情報の一覧。
    """
    rows = _hpc_linux_users_snapshot()
    api_semaphore = asyncio.Semaphore(_HPC_ADMIN_API_STATUS_CONCURRENCY)
    storage_semaphore = asyncio.Semaphore(_HPC_ADMIN_STORAGE_CONCURRENCY)

    async def api_state(username: str) -> tuple[str, str]:
        """指定ユーザーのLLM API状態を非同期で取得する。

        Args:
            username: 状態を取得するLinuxユーザー名。

        Returns:
            状態と画面表示用エラーメッセージの組。
        """
        if not _hpc_litellm_enabled():
            return "unknown", "LiteLLM Admin APIが未設定です"
        async with api_semaphore:
            return await asyncio.to_thread(_hpc_litellm_access_state, username)

    async def storage_usage(home: str) -> tuple[int | None, str | None]:
        """ホームディレクトリの使用量を非同期で取得する。

        Args:
            home: 集計対象のホームディレクトリ。

        Returns:
            使用バイト数とエラーメッセージの組。
        """
        async with storage_semaphore:
            return await asyncio.to_thread(_hpc_home_storage_usage, home)

    async def enrich(row: dict) -> dict:
        """ユーザー情報へLLM API状態とストレージ使用量を追加する。

        Args:
            row: Linuxユーザーの基本情報。

        Returns:
            管理画面向け情報を追加したユーザー情報。
        """
        updated = dict(row)
        (access, access_message), (used_bytes, storage_error) = await asyncio.gather(
            api_state(row["username"]),
            storage_usage(row["home"]),
        )
        updated["api_access"] = access
        updated["api_access_message"] = access_message
        updated["storage_used_bytes"] = used_bytes
        updated["storage_used_label"] = (
            _hpc_format_storage_bytes(used_bytes) if used_bytes is not None else "確認不可"
        )
        updated["storage_message"] = storage_error or ""
        return updated

    return list(await asyncio.gather(*(enrich(row) for row in rows)))

def _hpc_log_user_admin_success(action: str, actor: str, target: str) -> None:
    """秘密値を含めず、ユーザー権限操作の成功を監査ログへ記録する。

    Args:
        action: 実行した操作名。
        actor: 操作を実行した管理者ユーザー名。
        target: 操作対象のLinuxユーザー名。
    """
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    HPC_USER_ADMIN_LOG.info(
        "timestamp=%s action=%s actor=%s target=%s",
        timestamp,
        action,
        actor,
        target,
    )

async def _hpc_stop_user_openwebui_servers(handler, username: str) -> str | None:
    """管理中spawnerと残存Slurm jobの両方からOpen WebUIを停止する。

    Args:
        handler: 対象のJupyterHub Handler。
        username: 対象のLinuxユーザー名。

    Returns:
        正常ならNone、停止に失敗した場合はエラーメッセージ。
    """
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
        users = await _hpc_admin_users_snapshot()
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
        self.write({"users": await _hpc_admin_users_snapshot()})

    @web.authenticated
    async def post(self):
        """ユーザー・APIアクセス・shared Ollamaの管理操作を実行する。"""
        self._require_admin()
        self.set_header("Cache-Control", "no-store")
        try:
            request = parse_json_request(self.request.body, HpcAdminUsersRequest)
        except HpcRequestValidationError as exc:
            return self._api_error(400, str(exc))
        action = request.action
        username = request.username
        actor = self.current_user.name

        if action == "create":
            err = _hpc_validate_username(username)
            if err:
                return self._api_error(400, err)
            display_name = request.display_name
            err = _hpc_validate_display_name(display_name)
            if err:
                return self._api_error(400, err)
            initial_password = _hpc_generate_password()
            grant_sudo = (
                HPC_PORTAL_GRANT_SUDO if request.sudo is None else request.sudo
            )
            err = _hpc_create_linux_user(
                username,
                initial_password,
                grant_sudo,
                display_name,
            )
            if err:
                return self._api_error(400, err)
            if grant_sudo:
                _hpc_log_user_admin_success("sudo_enable", actor, username)
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

        if action == "display_name":
            if not username:
                return self._api_error(400, "username が必要です")
            display_name = request.display_name
            err = _hpc_set_linux_display_name(username, display_name)
            if err:
                return self._api_error(400, err)
            self.write({"ok": True, "username": username, "display_name": display_name})
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

        if action in {"sudo_enable", "sudo_disable"}:
            if not username:
                return self._api_error(400, "username が必要です")
            enabled = action == "sudo_enable"
            if not enabled and username in HPC_PORTAL_PROTECTED_USERS:
                return self._api_error(400, "保護されたユーザーのsudo権限は解除できません")
            if not enabled and username == actor:
                return self._api_error(400, "ログイン中の自分自身のsudo権限は解除できません")
            err = _hpc_set_linux_sudo(username, enabled)
            if err:
                return self._api_error(400, err)
            _hpc_log_user_admin_success(action, actor, username)
            self.write({"ok": True, "username": username, "sudo_enabled": enabled})
            return

        if action in {"api_disable", "api_enable"}:
            if not username:
                return self._api_error(400, "username が必要です")
            if username in HPC_PORTAL_PROTECTED_USERS:
                return self._api_error(400, "保護されたユーザーのLLM APIは変更できません")
            enabled = action == "api_enable"
            api_key, err = _hpc_litellm_admin_set_api_access(username, enabled)
            stop_err = None
            if not enabled:
                # key blockが一部失敗しても、起動中Open WebUIの停止は必ず試行する。
                stop_err = await _hpc_stop_user_openwebui_servers(self, username)
            errors = [error for error in (err, stop_err) if error]
            if errors:
                return self._api_error(400, "; ".join(errors))
            response = {"ok": True, "enabled": enabled}
            if api_key:
                response["api_key"] = api_key
                response["api_base_url"] = HPC_LITELLM_PUBLIC_BASE_URL
            self.write(response)
            return

        if action == "ollama_register_model":
            model = request.model
            exists, err = await asyncio.to_thread(_hpc_ollama_has_model, model)
            if err or not exists:
                return self._api_error(400, err or "Ollamaにモデルがありません")
            registration, err = await asyncio.to_thread(
                _hpc_litellm_register_ollama_model, model
            )
            if err:
                return self._api_error(400, err)
            self.write({"ok": True, "data": registration})
            return

        if action == "ollama_sync_models":
            result, err = await asyncio.to_thread(_hpc_litellm_sync_ollama_models)
            if err:
                return self._api_error(400, err)
            self.write({"ok": True, "data": result})
            return

        if action == "ollama_delete":
            model = request.model
            tags, err = await asyncio.to_thread(_hpc_ollama_cmd, "tags")
            if err:
                return self._api_error(400, err)
            exists = any(
                isinstance(item, dict) and str(item.get("name") or "") == model
                for item in (tags or {}).get("models", []) or []
            )
            litellm_err = await asyncio.to_thread(
                _hpc_litellm_delete_ollama_model, model
            )
            if litellm_err:
                return self._api_error(
                    400, "LiteLLMモデルを削除できませんでした: " + litellm_err
                )
            if exists:
                _data, err = await asyncio.to_thread(_hpc_ollama_cmd, "delete", model)
                if err:
                    await asyncio.to_thread(_hpc_litellm_register_ollama_model, model)
                    return self._api_error(400, err)
            # pull完了との競合で再登録された場合も、Ollama削除後にもう一度回収する。
            litellm_err = await asyncio.to_thread(
                _hpc_litellm_delete_ollama_model, model
            )
            if litellm_err:
                return self._api_error(
                    400, "LiteLLMモデルを削除できませんでした: " + litellm_err
                )
            self.write({"ok": True, "data": {"model": model, "deleted": True}})
            return

        if action in {"ollama_start", "ollama_stop", "ollama_update_check", "ollama_update", "ollama_status", "ollama_tags", "ollama_pull", "ollama_pull_cancel", "ollama_pull_status"}:
            mapping = {
                "ollama_start": "start",
                "ollama_stop": "stop",
                "ollama_update_check": "update-check",
                "ollama_update": "update",
                "ollama_status": "status",
                "ollama_tags": "tags",
                "ollama_pull": "pull",
                "ollama_pull_cancel": "pull-cancel",
                "ollama_pull_status": "pull-status",
            }
            model = request.model
            if action == "ollama_pull_status":
                data, err = await asyncio.to_thread(
                    _hpc_ollama_pull_progress, model or None
                )
                if not err and data and data.get("state") == "completed":
                    completed_model = str(data.get("model") or model).strip()
                    registration, registration_err = await asyncio.to_thread(
                        _hpc_litellm_register_ollama_model, completed_model
                    )
                    data["litellm_registration"] = registration or {
                        "state": "failed",
                        "model": completed_model,
                        "message": registration_err or "LiteLLM登録に失敗しました",
                    }
            else:
                data, err = await asyncio.to_thread(
                    _hpc_ollama_cmd,
                    mapping[action],
                    model if action in {"ollama_pull", "ollama_pull_cancel"} else None,
                    request.cpus if action == "ollama_start" else None,
                    request.memory if action == "ollama_start" else None,
                    request.parallel if action == "ollama_start" else None,
                    request.max_loaded_models if action == "ollama_start" else None,
                    request.context_length if action == "ollama_start" else None,
                    request.kv_cache_type if action == "ollama_start" else None,
                    request.keep_alive if action == "ollama_start" else None,
                    request.max_queue if action == "ollama_start" else None,
                    request.flash_attention if action == "ollama_start" else None,
                )
            if err:
                return self._api_error(400, err)
            if action == "ollama_pull":
                _hpc_start_ollama_registration_watcher(model)
            self.write({"ok": True, "data": data})
            return

        self._api_error(400, "不明な action です")
