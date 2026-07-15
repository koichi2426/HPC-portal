"""利用者向けLiteLLM Virtual KeyとAPI利用状態の管理を提供する。"""

import pwd
import threading
import urllib.parse

from ..common import (
    _HPC_EXTERNAL_API_KEY_LOCKS,
    _HPC_EXTERNAL_API_KEY_LOCKS_GUARD,
)
from .client import (
    _hpc_litellm_enabled,
    _hpc_litellm_request,
    _hpc_log_litellm_action,
    _hpc_safe_litellm_error,
)
from .openwebui import (
    _hpc_litellm_set_openwebui_key_blocked,
    _hpc_remove_openwebui_key,
)
from .users import (
    _hpc_litellm_ensure_user,
    _hpc_litellm_metadata,
    _hpc_litellm_set_user_admin_disabled,
    _hpc_litellm_user_admin_disabled,
)

def _hpc_litellm_generate_key(username: str) -> tuple[str | None, str | None]:
    """利用者向け外部API用Virtual Keyを発行する。

    Args:
        username: Keyを所有するLinuxユーザー名。

    Returns:
        ``(平文Key, エラー)``。成功時のエラーはNone。
    """
    if not _hpc_litellm_enabled():
        return None, "LiteLLM Admin API が未設定のため API key は未発行です"
    user_err = _hpc_litellm_ensure_user(username)
    if user_err:
        return None, user_err
    payload = {
        "user_id": username,
        "key_alias": username,
        "metadata": {
            "linux_username": username,
            "source": "hpc-portal",
            "admin_disabled": False,
        },
    }
    try:
        data = _hpc_litellm_request("/key/generate", payload)
    except RuntimeError as exc:
        return None, str(exc)
    key = data.get("key") or data.get("token")
    if not key:
        return None, "LiteLLM key 発行レスポンスに key が含まれていません"
    return key, None

def _hpc_litellm_iter_key_records(value):
    """LiteLLMレスポンスからKeyレコードを再帰的に列挙する。

    Args:
        value: LiteLLM APIのJSON互換値。

    Yields:
        Key識別子を含む辞書。
    """
    if isinstance(value, list):
        for item in value:
            yield from _hpc_litellm_iter_key_records(item)
        return
    if not isinstance(value, dict):
        return
    if any(k in value for k in ("token", "key", "key_alias", "hashed_token")):
        yield value
    for key in ("keys", "data", "key_list", "keys_info", "tokens", "info"):
        child = value.get(key)
        if child is not None and child is not value:
            yield from _hpc_litellm_iter_key_records(child)

def _hpc_litellm_key_identifier(record: dict) -> str:
    """Key操作に使える識別子をレコードから取得する。

    Args:
        record: LiteLLMのKeyレコード。

    Returns:
        利用可能な識別子。存在しなければ空文字列。
    """
    for field in ("key", "token", "key_name", "hashed_token", "token_id", "id"):
        value = record.get(field)
        if value:
            return str(value)
    return ""

def _hpc_litellm_key_belongs_to_user(record: dict, username: str) -> bool:
    """Keyレコードが利用者に属するか判定する。

    Args:
        record: LiteLLMのKeyレコード。
        username: Linuxユーザー名。

    Returns:
        user_id、alias、metadataのいずれかが一致すればTrue。
    """
    metadata = _hpc_litellm_metadata(record.get("metadata"))
    candidates = {
        str(record.get("user_id") or ""),
        str(record.get("key_alias") or ""),
        str(metadata.get("linux_username") or ""),
    }
    return username in candidates

def _hpc_litellm_list_user_keys(username: str) -> tuple[list[dict], str | None]:
    """利用者に属するLiteLLM Keyを列挙する。

    Args:
        username: 検索対象のuser_idまたはLinuxユーザー名。

    Returns:
        ``(Keyレコード一覧, エラー)``。
    """
    if not _hpc_litellm_enabled():
        return [], "LiteLLM Admin API が未設定です"
    quoted = urllib.parse.quote(username, safe="")
    errors = []
    succeeded = False
    for path in (f"/key/list?user_id={quoted}", "/key/list", f"/user/info?user_id={quoted}"):
        try:
            data = _hpc_litellm_request(path, method="GET")
        except RuntimeError as exc:
            errors.append(str(exc))
            continue
        succeeded = True
        records = [
            rec for rec in _hpc_litellm_iter_key_records(data)
            if _hpc_litellm_key_belongs_to_user(rec, username)
        ]
        if records:
            return records, None
    return [], errors[-1] if errors and not succeeded else None

def _hpc_litellm_is_portal_external_key(record: dict, username: str) -> bool:
    """ポータルで予約した外部API keyのaliasだけを再発行対象にする。

    Args:
        record: LiteLLMのKeyレコード。
        username: 対象のLinuxユーザー名。

    Returns:
        ポータル発行の外部API KeyならTrue。
    """
    # LiteLLMの一覧APIはバージョンによってmetadataを返さないことがあるため、
    # metadata.sourceではなく予約済みのaliasを正本にする。
    # Open WebUI用は常に ``openwebui-<username>`` であり、この条件に一致しない。
    return str(record.get("key_alias") or "") == username

def _hpc_litellm_user_external_api_state(username: str) -> tuple[str, str | None]:
    """管理画面向けに外部APIの利用状態を取得する。

    Args:
        username: 状態を取得するLinuxユーザー名。

    Returns:
        ``(状態, エラー)``。状態は enabled / disabled / unissued / unknown。
    """
    disabled, err = _hpc_litellm_user_admin_disabled(username)
    if err:
        lowered = str(err).lower()
        if "http 404" in lowered or "not found" in lowered:
            return "unissued", None
        return "unknown", _hpc_safe_litellm_error(err)
    if disabled:
        return "disabled", None
    records, err = _hpc_litellm_list_user_keys(username)
    if err:
        return "unknown", _hpc_safe_litellm_error(err)
    if any(_hpc_litellm_is_portal_external_key(record, username) for record in records):
        return "enabled", None
    return "unissued", None

def _hpc_litellm_external_api_key_lock(username: str):
    """同じユーザーの外部API key再発行を直列化する。

    Args:
        username: 対象のLinuxユーザー名。

    Returns:
        ユーザー単位の排他ロック。
    """
    with _HPC_EXTERNAL_API_KEY_LOCKS_GUARD:
        return _HPC_EXTERNAL_API_KEY_LOCKS.setdefault(username, threading.Lock())

def _hpc_litellm_delete_portal_external_keys(username: str) -> str | None:
    """再発行前にポータル発行の外部API keyをblockして削除・確認する。

    Open WebUI用（source=hpc-portal-openwebui）のkeyには一切触れない。

    Args:
        username: 対象のLinuxユーザー名。

    Returns:
        正常ならNone、失敗時はエラーメッセージ。
    """
    records, err = _hpc_litellm_list_user_keys(username)
    if err:
        return err
    targets = [
        record for record in records
        if _hpc_litellm_is_portal_external_key(record, username)
    ]
    if not targets:
        return None

    key_ids = []
    for record in targets:
        key_id = _hpc_litellm_key_identifier(record)
        if key_id:
            key_ids.append(key_id)

    for key_id in key_ids:
        try:
            _hpc_litellm_request("/key/block", {"key": key_id})
        except RuntimeError as exc:
            safe_error = _hpc_safe_litellm_error(exc)
            _hpc_log_litellm_action("api_key_regenerate_block", username, "failed", safe_error)
            return safe_error

    try:
        # LiteLLMはalias指定で削除できる。key値やhashの形式差に依存しないため、
        # 旧バージョンの一覧レスポンスでも確実に同じaliasを解放できる。
        _hpc_litellm_request("/key/delete", {"key_aliases": [username]})
    except RuntimeError as exc:
        safe_error = _hpc_safe_litellm_error(exc)
        _hpc_log_litellm_action("api_key_regenerate_delete", username, "failed", safe_error)
        return safe_error

    remaining, list_err = _hpc_litellm_list_user_keys(username)
    if list_err:
        safe_error = _hpc_safe_litellm_error(list_err)
        _hpc_log_litellm_action("api_key_regenerate_verify", username, "failed", safe_error)
        return safe_error
    if any(_hpc_litellm_is_portal_external_key(record, username) for record in remaining):
        message = "古い外部API keyの削除を確認できません"
        _hpc_log_litellm_action("api_key_regenerate_verify", username, "failed", message)
        return message
    _hpc_log_litellm_action("api_key_regenerate_delete", username, "ok")
    return None

def _hpc_litellm_set_key_metadata(record: dict, disabled: bool) -> str | None:
    """Keyの補助metadataへ管理者停止状態を反映する。

    Args:
        record: 更新対象のKeyレコード。
        disabled: 停止状態にする場合はTrue。

    Returns:
        正常ならNone、失敗時はエラーメッセージ。
    """
    key_id = _hpc_litellm_key_identifier(record)
    if not key_id:
        return "LiteLLM key の識別子が取得できません"
    metadata = dict(_hpc_litellm_metadata(record.get("metadata")))
    metadata.update({
        "linux_username": metadata.get("linux_username") or record.get("user_id") or record.get("key_alias") or "",
        "source": metadata.get("source") or "hpc-portal",
        "admin_disabled": bool(disabled),
    })
    try:
        _hpc_litellm_request("/key/update", {"key": key_id, "metadata": metadata})
        return None
    except RuntimeError as exc:
        return str(exc)

def _hpc_litellm_set_user_keys_blocked(
    username: str,
    blocked: bool,
    *,
    mark_admin_disabled: bool | None = None,
    include_openwebui: bool = True,
) -> str | None:
    """利用者に属するVirtual Keyを一括でblockまたはunblockする。

    Args:
        username: Linuxユーザー名。
        blocked: blockする場合はTrue。
        mark_admin_disabled: Key metadataへ記録する停止状態。
        include_openwebui: Open WebUI専用Keyも対象にするか。

    Returns:
        正常ならNone、一部でも失敗した場合はエラーメッセージ。
    """
    records, err = _hpc_litellm_list_user_keys(username)
    if err:
        return err
    endpoint = "/key/block" if blocked else "/key/unblock"
    action = "user_keys_block" if blocked else "user_keys_unblock"
    errors = []
    for record in records:
        metadata = _hpc_litellm_metadata(record.get("metadata"))
        if not include_openwebui and metadata.get("source") == "hpc-portal-openwebui":
            continue
        key_id = _hpc_litellm_key_identifier(record)
        if not key_id:
            errors.append("LiteLLM key の識別子が取得できません")
            continue
        try:
            _hpc_litellm_request(endpoint, {"key": key_id})
        except RuntimeError as exc:
            errors.append(str(exc))
            continue
        if mark_admin_disabled is not None:
            meta_err = _hpc_litellm_set_key_metadata(record, mark_admin_disabled)
            if meta_err:
                # 管理者停止フラグの正本は user metadata。key metadata は一覧確認用の補助なので、
                # LiteLLM のバージョン差で更新に失敗しても block/unblock 成功を優先する。
                pass
    if errors:
        joined = "; ".join(_hpc_safe_litellm_error(error) for error in errors)
        _hpc_log_litellm_action(action, username, "failed", joined)
        return joined
    _hpc_log_litellm_action(action, username, "ok")
    return None

def _hpc_litellm_ensure_external_api_key(username: str) -> tuple[str | None, str | None]:
    """ポータル用外部API keyがなければ新規発行する。

    Args:
        username: keyを所有するLinuxユーザー名。

    Returns:
        新規発行した平文keyとエラーメッセージの組。既存keyがある場合は
        どちらもNoneを返す。
    """
    with _hpc_litellm_external_api_key_lock(username):
        records, err = _hpc_litellm_list_user_keys(username)
        if err:
            return None, err
        if any(_hpc_litellm_is_portal_external_key(record, username) for record in records):
            return None, None
        return _hpc_litellm_generate_key(username)

def _hpc_litellm_admin_set_api_access(
    username: str,
    enabled: bool,
) -> tuple[str | None, str | None]:
    """利用者単位で外部APIとOpen WebUIの利用可否を切り替える。

    Args:
        username: 対象のLinuxユーザー名。
        enabled: 有効化する場合はTrue。

    Returns:
        ``(新規発行したAPI key, エラー)``。既存keyの再有効化時はkeyを返さない。
    """
    if not _hpc_litellm_enabled():
        return None, "LiteLLM Admin API が未設定です"
    try:
        pwd.getpwnam(username)
    except KeyError:
        return None, "ユーザーが見つかりません"
    if not enabled:
        # 先にユーザーを停止状態へし、新規Open WebUI起動を拒否する。
        user_err = _hpc_litellm_set_user_admin_disabled(username, True)
        if user_err:
            _hpc_log_litellm_action("api_disable", username, "failed", user_err)
            return None, user_err
        external_err = _hpc_litellm_set_user_keys_blocked(
            username,
            blocked=True,
            mark_admin_disabled=True,
            include_openwebui=False,
        )
        openwebui_err = _hpc_litellm_set_openwebui_key_blocked(username, True)
        errors = [error for error in (external_err, openwebui_err) if error]
        if errors:
            joined = "; ".join(errors)
            _hpc_log_litellm_action("api_disable", username, "partial", joined)
            return None, joined
        _hpc_log_litellm_action("api_disable", username, "ok")
        return None, None

    # 未登録ユーザーも同じ操作で有効化できるよう、先にLiteLLMユーザーを用意する。
    user_err = _hpc_litellm_ensure_user(username)
    if user_err:
        _hpc_log_litellm_action("api_enable", username, "failed", user_err)
        return None, user_err

    # 再有効化では保存済みの同じOpen WebUI keyをunblockする。どれかが失敗したら
    # user metadataは停止状態のままにし、部分的な有効化を避ける。
    external_err = _hpc_litellm_set_user_keys_blocked(
        username,
        blocked=False,
        mark_admin_disabled=False,
        include_openwebui=False,
    )
    openwebui_err = _hpc_litellm_set_openwebui_key_blocked(username, False)
    errors = [error for error in (external_err, openwebui_err) if error]
    if errors:
        joined = "; ".join(errors)
        _hpc_litellm_set_user_admin_disabled(username, True)
        _hpc_litellm_set_user_keys_blocked(
            username,
            blocked=True,
            mark_admin_disabled=True,
            include_openwebui=False,
        )
        _hpc_litellm_set_openwebui_key_blocked(username, True)
        _hpc_log_litellm_action("api_enable", username, "failed", joined)
        return None, joined
    user_err = _hpc_litellm_set_user_admin_disabled(username, False)
    if user_err:
        # user metadata更新失敗時は、先に有効化したkeyを再停止する。
        _hpc_litellm_set_user_keys_blocked(
            username,
            blocked=True,
            mark_admin_disabled=True,
            include_openwebui=False,
        )
        _hpc_litellm_set_openwebui_key_blocked(username, True)
        _hpc_log_litellm_action("api_enable", username, "failed", user_err)
        return None, user_err

    api_key, key_err = _hpc_litellm_ensure_external_api_key(username)
    if key_err:
        # 有効化とkey発行を一操作として扱い、発行失敗時は停止状態へ戻す。
        _hpc_litellm_set_user_admin_disabled(username, True)
        _hpc_litellm_set_user_keys_blocked(
            username,
            blocked=True,
            mark_admin_disabled=True,
            include_openwebui=False,
        )
        _hpc_litellm_set_openwebui_key_blocked(username, True)
        safe_error = _hpc_safe_litellm_error(key_err)
        _hpc_log_litellm_action("api_enable_key_generate", username, "failed", safe_error)
        return None, safe_error
    if api_key:
        _hpc_log_litellm_action("api_enable_key_generate", username, "ok")
    _hpc_log_litellm_action("api_enable", username, "ok")
    return api_key, None

def _hpc_litellm_regenerate_own_key(username: str) -> tuple[str | None, str | None]:
    """外部API用Keyを安全に削除して同じaliasで再発行する。

    Args:
        username: 再発行する本人のLinuxユーザー名。

    Returns:
        ``(新しい平文Key, エラー)``。Open WebUI Keyは変更しない。
    """
    with _hpc_litellm_external_api_key_lock(username):
        if not _hpc_litellm_enabled():
            return None, "LiteLLM Admin API が未設定です"
        try:
            pwd.getpwnam(username)
        except KeyError:
            return None, "ユーザーが見つかりません"
        disabled, err = _hpc_litellm_user_admin_disabled(username)
        if err:
            return None, err
        if disabled:
            return None, "API key は管理者により無効化されています"
        err = _hpc_litellm_delete_portal_external_keys(username)
        if err:
            return None, err
        key, err = _hpc_litellm_generate_key(username)
        if err:
            _hpc_log_litellm_action("api_key_regenerate_generate", username, "failed", err)
            return None, err
        _hpc_log_litellm_action("api_key_regenerate_generate", username, "ok")
        return key, None

def _hpc_litellm_delete_user_keys(username: str) -> str | None:
    """ユーザー削除時に関連Virtual Keyと保存ファイルを破棄する。

    Args:
        username: 削除対象のLinuxユーザー名。

    Returns:
        正常ならNone、無効化または削除失敗時はエラーメッセージ。
    """
    _hpc_remove_openwebui_key(username)
    if not _hpc_litellm_enabled():
        return "LiteLLM Admin API が未設定のため key 無効化は未確認です"
    block_err = _hpc_litellm_set_user_keys_blocked(username, blocked=True, mark_admin_disabled=True)
    payload_candidates = (
        {"user_ids": [username]},
        {"user_id": username},
    )
    last_err = None
    for payload in payload_candidates:
        try:
            _hpc_litellm_request("/user/delete", payload)
            return block_err
        except RuntimeError as exc:
            last_err = str(exc)
    return block_err or last_err or "LiteLLM user/key 無効化に失敗しました"
