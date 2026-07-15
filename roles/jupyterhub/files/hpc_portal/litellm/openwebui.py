"""Open WebUI専用LiteLLM keyの発行・保存・状態管理を提供する。"""

import os
import secrets
import urllib.parse

from ..common import OPENWEBUI_LITELLM_KEY_DIR
from ..users import _hpc_validate_username
from .client import (
    _hpc_litellm_enabled,
    _hpc_litellm_request,
    _hpc_log_litellm_action,
    _hpc_safe_litellm_error,
)
from .users import _hpc_litellm_ensure_user, _hpc_litellm_user_admin_disabled

def _hpc_openwebui_key_path(username: str) -> str:
    """Open WebUI Keyの保存先を返す。

    Args:
        username: Linuxユーザー名。

    Returns:
        root専用Keyファイルの絶対パス。

    Raises:
        ValueError: ユーザー名がKey保存先として不正な場合。
    """
    if _hpc_validate_username(username):
        raise ValueError("Open WebUI key 用のユーザー名が不正です")
    return os.path.join(OPENWEBUI_LITELLM_KEY_DIR, f"{username}.key")

def _hpc_read_openwebui_key(username: str) -> str:
    """保存済みOpen WebUI Keyを読む。

    Args:
        username: Linuxユーザー名。

    Returns:
        Key文字列。未保存または読込失敗時は空文字列。
    """
    try:
        with open(_hpc_openwebui_key_path(username), "r", encoding="utf-8") as key_file:
            return key_file.read().strip()
    except OSError:
        return ""

def _hpc_write_openwebui_key(username: str, key: str) -> str | None:
    """Open WebUI Keyをroot専用ファイルへ原子的に保存する。

    Args:
        username: Linuxユーザー名。
        key: 保存する平文Virtual Key。

    Returns:
        正常ならNone、失敗時はエラーメッセージ。
    """
    if not key:
        return "Open WebUI 用 key が空です"
    try:
        os.makedirs(OPENWEBUI_LITELLM_KEY_DIR, mode=0o700, exist_ok=True)
        path = _hpc_openwebui_key_path(username)
        tmp_path = f"{path}.tmp-{os.getpid()}-{secrets.token_hex(6)}"
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, (key + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp_path, path)
        os.chmod(path, 0o600)
    except OSError as exc:
        try:
            if "tmp_path" in locals():
                os.unlink(tmp_path)
        except OSError:
            pass
        return f"Open WebUI 用 key の保存に失敗しました: {exc}"
    return None

def _hpc_remove_openwebui_key(username: str) -> None:
    """保存済みOpen WebUI Keyファイルを削除する。

    Args:
        username: Linuxユーザー名。
    """
    try:
        os.unlink(_hpc_openwebui_key_path(username))
    except FileNotFoundError:
        pass
    except OSError:
        pass

def _hpc_litellm_generate_openwebui_key(username: str) -> tuple[str | None, str | None]:
    """ユーザーごとに再利用する Open WebUI 専用 Virtual Key を初回だけ発行する。

    Args:
        username: 対象のLinuxユーザー名。

    Returns:
        発行したKeyとエラーメッセージの組。
    """
    if not _hpc_litellm_enabled():
        return None, "LiteLLM Admin API が未設定です"
    user_err = _hpc_litellm_ensure_user(username)
    if user_err:
        return None, user_err
    disabled, err = _hpc_litellm_user_admin_disabled(username)
    if err:
        return None, err
    if disabled:
        return None, "API 利用が管理者により停止されています"
    payload = {
        "user_id": username,
        "key_alias": f"openwebui-{username}",
        "metadata": {
            "linux_username": username,
            "source": "hpc-portal-openwebui",
            "admin_disabled": False,
        },
    }
    try:
        data = _hpc_litellm_request("/key/generate", payload)
    except RuntimeError as exc:
        return None, str(exc)
    key = data.get("key") or data.get("token")
    if not key:
        return None, "Open WebUI 用 key 発行レスポンスに key が含まれていません"
    _hpc_log_litellm_action("openwebui_key_generate", username, "ok")
    return key, None

def _hpc_litellm_openwebui_key_info(username: str, key: str) -> tuple[dict | None, str, str | None]:
    """保存keyのLiteLLM状態を返す: valid / missing / blocked / mismatch / error。

    Args:
        username: 対象のLinuxユーザー名。
        key: LiteLLM Virtual Key。

    Returns:
        Key情報、状態、エラーメッセージの組。
    """
    quoted = urllib.parse.quote(key, safe="")
    try:
        data = _hpc_litellm_request(f"/key/info?key={quoted}", method="GET")
    except RuntimeError as exc:
        message = str(exc)
        lowered = message.lower()
        if any(marker in lowered for marker in ("http 401", "http 404", "token_not_found", "not found")):
            return None, "missing", None
        return None, "error", _hpc_safe_litellm_error(exc)
    info = data.get("info") if isinstance(data, dict) else None
    if not isinstance(info, dict):
        info = data if isinstance(data, dict) else {}
    record_user_id = str(info.get("user_id") or data.get("user_id") or "")
    if not record_user_id:
        return info, "mismatch", "保存keyにuser_idが設定されていません"
    if record_user_id != username:
        return info, "mismatch", "保存keyのuser_idがログインユーザーと一致しません"
    blocked_value = info.get("blocked", data.get("blocked"))
    blocked = blocked_value is True or str(blocked_value).strip().lower() in {"true", "1", "yes"}
    if blocked:
        return info, "blocked", None
    return info, "valid", None

def _hpc_litellm_set_openwebui_key_blocked(username: str, blocked: bool) -> str | None:
    """保存済みOpen WebUI Keyをblockまたはunblockする。

    Args:
        username: Linuxユーザー名。
        blocked: blockする場合はTrue。

    Returns:
        正常ならNone、失敗時は安全化したエラーメッセージ。
    """
    key = _hpc_read_openwebui_key(username)
    if not key:
        return None
    endpoint = "/key/block" if blocked else "/key/unblock"
    action = "openwebui_key_block" if blocked else "openwebui_key_unblock"
    try:
        _hpc_litellm_request(endpoint, {"key": key})
    except RuntimeError as exc:
        message = str(exc)
        lowered = message.lower()
        if any(marker in lowered for marker in ("http 401", "http 404", "token_not_found", "not found")):
            _hpc_remove_openwebui_key(username)
            _hpc_log_litellm_action(action, username, "missing")
            return None
        safe_error = _hpc_safe_litellm_error(exc)
        _hpc_log_litellm_action(action, username, "failed", safe_error)
        return safe_error
    _hpc_log_litellm_action(action, username, "ok")
    return None

def _hpc_litellm_get_openwebui_key(username: str) -> tuple[str | None, str | None]:
    """有効な利用者の永続Open WebUI Keyを取得または発行する。

    Args:
        username: Open WebUIを起動するLinuxユーザー名。

    Returns:
        ``(平文Key, エラー)``。成功時のエラーはNone。
    """
    if not _hpc_litellm_enabled():
        return None, "LiteLLM Admin API が未設定です"
    user_err = _hpc_litellm_ensure_user(username)
    if user_err:
        return None, user_err
    disabled, err = _hpc_litellm_user_admin_disabled(username)
    if err:
        return None, err
    if disabled:
        return None, "API 利用が管理者により停止されています"
    existing_key = _hpc_read_openwebui_key(username)
    if existing_key:
        _info, state, info_error = _hpc_litellm_openwebui_key_info(username, existing_key)
        if state == "valid":
            return existing_key, None
        if state == "missing":
            _hpc_remove_openwebui_key(username)
            _hpc_log_litellm_action("openwebui_key_validate", username, "missing")
        elif state == "blocked":
            _hpc_log_litellm_action("openwebui_key_validate", username, "blocked")
            return None, "Open WebUI 用 API key が無効化されています"
        elif state == "mismatch":
            _hpc_log_litellm_action("openwebui_key_validate", username, "mismatch", info_error)
            return None, info_error or "Open WebUI 用 API key の所有者が一致しません"
        else:
            _hpc_log_litellm_action("openwebui_key_validate", username, "failed", info_error)
            return None, info_error or "Open WebUI 用 API key の確認に失敗しました"
    key, err = _hpc_litellm_generate_openwebui_key(username)
    if err:
        return None, err
    write_err = _hpc_write_openwebui_key(username, key or "")
    if write_err:
        try:
            _hpc_litellm_request("/key/block", {"key": key})
        except RuntimeError:
            pass
        _hpc_log_litellm_action("openwebui_key_store", username, "failed", write_err)
        return None, write_err
    _hpc_log_litellm_action("openwebui_key_store", username, "ok")
    return key, None
