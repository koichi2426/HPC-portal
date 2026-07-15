"""LiteLLMユーザーと管理者停止状態の管理を提供する。"""

import json
import urllib.parse

from .client import _hpc_litellm_enabled, _hpc_litellm_request

def _hpc_litellm_ensure_user(username: str) -> str | None:
    """LiteLLMユーザーが存在する状態を保証する。

    Args:
        username: Linuxユーザー名と共通のuser_id。

    Returns:
        正常または既存ならNone、失敗時はエラーメッセージ。
    """
    if not _hpc_litellm_enabled():
        return "LiteLLM Admin API が未設定のため API key は未発行です"
    payload = {
        "user_id": username,
        "user_email": f"{username}@hpc-portal.local",
        "user_role": "internal_user",
        "metadata": {
            "linux_username": username,
            "source": "hpc-portal",
            "admin_disabled": False,
        },
    }
    try:
        _hpc_litellm_request("/user/new", payload)
        return None
    except RuntimeError as exc:
        # 既に存在する場合は key 発行に進める。LiteLLM の重複エラー文言はバージョンで揺れるため広めに許容する。
        msg = str(exc)
        if "already" in msg.lower() or "exists" in msg.lower() or "duplicate" in msg.lower():
            return None
        return msg

def _hpc_litellm_metadata(value) -> dict:
    """LiteLLMのmetadata値を辞書へ正規化する。

    Args:
        value: 辞書またはJSON文字列。

    Returns:
        metadata辞書。不正値の場合は空辞書。
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {}
    return {}

def _hpc_litellm_user_metadata(username: str) -> tuple[dict, str | None]:
    """LiteLLMユーザーのmetadataを取得する。

    Args:
        username: Linuxユーザー名。

    Returns:
        ``(metadata, エラー)``。
    """
    if not _hpc_litellm_enabled():
        return {}, "LiteLLM Admin API が未設定です"
    quoted = urllib.parse.quote(username, safe="")
    try:
        data = _hpc_litellm_request(f"/user/info?user_id={quoted}", method="GET")
    except RuntimeError as exc:
        return {}, str(exc)
    candidates = [
        data,
        data.get("user_info") if isinstance(data, dict) else None,
        data.get("info") if isinstance(data, dict) else None,
    ]
    for item in candidates:
        if isinstance(item, dict):
            metadata = _hpc_litellm_metadata(item.get("metadata"))
            if metadata:
                return metadata, None
    return {}, None

def _hpc_litellm_set_user_admin_disabled(username: str, disabled: bool) -> str | None:
    """ポータル管理者による停止状態をユーザーmetadataへ保存する。

    Args:
        username: Linuxユーザー名。
        disabled: 停止状態にする場合はTrue。

    Returns:
        正常ならNone、失敗時はエラーメッセージ。
    """
    err = _hpc_litellm_ensure_user(username)
    if err:
        return err
    payload = {
        "user_id": username,
        "metadata": {
            "linux_username": username,
            "source": "hpc-portal",
            "admin_disabled": bool(disabled),
        },
    }
    try:
        _hpc_litellm_request("/user/update", payload)
        return None
    except RuntimeError as exc:
        return str(exc)

def _hpc_litellm_user_admin_disabled(username: str) -> tuple[bool, str | None]:
    """ユーザーが管理者により停止されているか取得する。

    Args:
        username: Linuxユーザー名。

    Returns:
        ``(停止状態, エラー)``。
    """
    metadata, err = _hpc_litellm_user_metadata(username)
    if err:
        return False, err
    # ユーザーmetadataを正本にする。過去にblockしたキーのmetadataを参照すると、
    # API利用を再有効化しても古いキーの停止フラグで誤って拒否されるため。
    return metadata.get("admin_disabled") is True, None
