"""LiteLLMのユーザー、Virtual Key、モデル情報を管理する。"""

from .common import (
    HPC_LITELLM_INTERNAL_BASE_URL,
    HPC_LITELLM_LOG,
    HPC_LITELLM_MASTER_KEY,
    HPC_OLLAMA_API_BASE,
    OPENWEBUI_LITELLM_KEY_DIR,
    _HPC_EXTERNAL_API_KEY_LOCKS,
    _HPC_EXTERNAL_API_KEY_LOCKS_GUARD,
    json,
    os,
    pwd,
    re,
    secrets,
    threading,
    urllib,
)
from .users import _hpc_validate_username

_HPC_LITELLM_MODEL_LOCKS: dict[str, threading.Lock] = {}
_HPC_LITELLM_MODEL_LOCKS_GUARD = threading.Lock()

def _hpc_litellm_enabled() -> bool:
    """LiteLLM管理APIを利用可能か判定する。

    Returns:
        内部URLと管理キーが設定済みならTrue。
    """
    return bool(HPC_LITELLM_INTERNAL_BASE_URL and HPC_LITELLM_MASTER_KEY)


def _hpc_litellm_request(path: str, payload: dict | None = None, method: str = "POST") -> dict:
    """LiteLLM管理APIへ認証付きリクエストを送る。

    Args:
        path: 内部Base URLからのAPIパス。
        payload: JSONとして送信する辞書。GETではNoneを指定する。
        method: HTTPメソッド。

    Returns:
        JSONレスポンスを辞書化した値。

    Raises:
        RuntimeError: 設定不足、HTTPエラー、通信失敗、JSON形式不正の場合。
    """
    if not _hpc_litellm_enabled():
        raise RuntimeError("LiteLLM Admin API が未設定です")
    url = HPC_LITELLM_INTERNAL_BASE_URL + path
    data = None
    headers = {
        "Authorization": f"Bearer {HPC_LITELLM_MASTER_KEY}",
        "Accept": "application/json",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LiteLLM API HTTP {exc.code}: {body[:300]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"LiteLLM API 接続失敗: {exc.reason}") from exc
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


def _hpc_litellm_model_records(value):
    """LiteLLM の model 一覧レスポンスから model record を取り出す。

    引数:
        value: LiteLLM API から返った JSON 互換オブジェクト。

    戻り値:
        model ID を含む可能性がある dict を順に返す generator。
    """
    if isinstance(value, dict):
        for key in ("data", "models", "model_list"):
            if key in value:
                yield from _hpc_litellm_model_records(value[key])
        if any(k in value for k in ("id", "model_name", "litellm_params")):
            yield value
        return
    if isinstance(value, list):
        for item in value:
            yield from _hpc_litellm_model_records(item)
        return
    if isinstance(value, str) and value.strip():
        yield {"id": value.strip()}


def _hpc_litellm_list_models() -> tuple[list[dict], str | None]:
    """ユーザー画面に表示する LiteLLM model 一覧を取得する。

    LiteLLM の `/models` はバージョンや設定によって response shape が揺れるため、
    複数の候補フィールドから model ID を取り出して重複を除去する。

    戻り値:
        1要素目は `id` と `owned_by` を持つ model dict の list。
        2要素目は取得失敗時のエラーメッセージ。成功時は None。
    """
    if not _hpc_litellm_enabled():
        return [], "LiteLLM Admin API が未設定です"
    try:
        data = _hpc_litellm_request("/models", method="GET")
    except RuntimeError as exc:
        return [], str(exc)
    models = []
    seen = set()
    for record in _hpc_litellm_model_records(data):
        model_id = (
            record.get("id")
            or record.get("model_name")
            or record.get("model")
            or ""
        )
        model_id = str(model_id).strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        models.append({
            "id": model_id,
            "owned_by": str(record.get("owned_by") or record.get("provider") or "").strip(),
        })
    models.sort(key=lambda item: item["id"])
    return models, None


def _hpc_litellm_model_lock(model: str) -> threading.Lock:
    """モデル単位のLiteLLM登録ロックを返す。

    Args:
        model: Ollamaモデル名。

    Returns:
        同一プロセス内の重複登録を防ぐロック。
    """
    with _HPC_LITELLM_MODEL_LOCKS_GUARD:
        return _HPC_LITELLM_MODEL_LOCKS.setdefault(model, threading.Lock())


def _hpc_litellm_register_ollama_model(model: str) -> tuple[dict | None, str | None]:
    """OllamaモデルをLiteLLMへ重複なく登録する。

    LiteLLMのモデル一覧に同名モデルがあれば登録済みとして扱う。未登録の場合は
    Ollamaのモデル名を公開名にし、DB保存される ``/model/new`` へ追加する。

    Args:
        model: Ollamaに登録済みのモデル名。

    Returns:
        ``(登録状態, エラー)``。登録状態の ``state`` は ``registered`` または
        ``already_registered``。
    """
    model = str(model or "").strip()
    if not model:
        return None, "モデル名が必要です"
    if re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", model) is None:
        return None, "モデル名に使用できない文字が含まれています"
    if not _hpc_litellm_enabled():
        return None, "LiteLLM Admin API が未設定です"

    with _hpc_litellm_model_lock(model):
        # 削除とpull完了が競合した場合に、Ollamaにないモデルを再登録しない。
        from .ollama import _hpc_ollama_has_model

        exists, ollama_err = _hpc_ollama_has_model(model)
        if ollama_err or not exists:
            return None, ollama_err or f"Ollamaにモデル {model} がありません"
        models, err = _hpc_litellm_list_models()
        if err:
            return None, err
        if any(item.get("id") == model for item in models):
            return {
                "state": "already_registered",
                "model": model,
                "message": "LiteLLM登録済み",
            }, None

        payload = {
            "model_name": model,
            "litellm_params": {
                "model": f"ollama/{model}",
                "api_base": HPC_OLLAMA_API_BASE,
            },
            "model_info": {
                "source": "hpc-portal-ollama",
            },
        }
        try:
            _hpc_litellm_request("/model/new", payload)
        except RuntimeError as exc:
            return None, _hpc_safe_litellm_error(exc)

        models, err = _hpc_litellm_list_models()
        if err:
            return None, f"登録後の確認に失敗しました: {err}"
        if not any(item.get("id") == model for item in models):
            return None, "LiteLLMへ追加しましたが、モデル一覧で確認できませんでした"
        HPC_LITELLM_LOG.info(
            "action=model_register model=%s backend=%s result=ok",
            model,
            f"ollama/{model}",
        )
        return {
            "state": "registered",
            "model": model,
            "message": "LiteLLMへ自動登録しました",
        }, None


def _hpc_litellm_delete_ollama_model(model: str) -> str | None:
    """Ollamaモデルに対応するLiteLLMのDBモデルを削除する。

    Args:
        model: Ollamaから削除するモデル名。

    Returns:
        正常または該当モデルなしならNone、失敗時はエラーメッセージ。
    """
    model = str(model or "").strip()
    if not model:
        return "モデル名が必要です"
    if re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", model) is None:
        return "モデル名に使用できない文字が含まれています"
    if not _hpc_litellm_enabled():
        return "LiteLLM Admin API が未設定です"

    with _hpc_litellm_model_lock(model):
        try:
            response = _hpc_litellm_request("/v1/model/info", method="GET")
        except RuntimeError as exc:
            return _hpc_safe_litellm_error(exc)
        deployment_ids = []
        config_model_found = False
        for record in _hpc_litellm_model_records(response):
            litellm_params = record.get("litellm_params") or {}
            model_info = record.get("model_info") or {}
            if not isinstance(litellm_params, dict) or not isinstance(model_info, dict):
                continue
            if str(record.get("model_name") or "") != model:
                continue
            if str(litellm_params.get("model") or "") != f"ollama/{model}":
                continue
            model_id = str(model_info.get("id") or "").strip()
            if model_id and bool(model_info.get("db_model")):
                deployment_ids.append(model_id)
            else:
                config_model_found = True

        if config_model_found:
            return "Ansible設定由来のLiteLLMモデルはポータルから削除できません"

        for model_id in dict.fromkeys(deployment_ids):
            try:
                _hpc_litellm_request("/model/delete", {"id": model_id})
            except RuntimeError as exc:
                return _hpc_safe_litellm_error(exc)
        if deployment_ids:
            HPC_LITELLM_LOG.info(
                "action=model_delete model=%s deployments=%d result=ok",
                model,
                len(set(deployment_ids)),
            )
        return None


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


def _hpc_openwebui_key_path(username: str) -> str:
    """Open WebUI Keyの保存先を返す。

    Args:
        username: Linuxユーザー名。

    Returns:
        root専用Keyファイルの絶対パス。
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


def _hpc_safe_litellm_error(error) -> str:
    """ログや画面へ返すエラーから Virtual Key らしい文字列を除去する。"""
    return re.sub(r"sk-[A-Za-z0-9._~-]+", "sk-[REDACTED]", str(error or ""))[:500]


def _hpc_log_litellm_action(action: str, username: str, result: str, error=None) -> None:
    """秘密値を除去してLiteLLM操作ログを記録する。

    Args:
        action: 操作名。
        username: 対象のLinuxユーザー名。
        result: ok、failed、partialなどの結果。
        error: 任意のエラー情報。
    """
    message = "action=%s user=%s result=%s"
    args = [action, username, result]
    if error:
        message += " error=%s"
        args.append(_hpc_safe_litellm_error(error))
    if result == "ok":
        HPC_LITELLM_LOG.info(message, *args)
    else:
        HPC_LITELLM_LOG.warning(message, *args)


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
    """ユーザーごとに再利用する Open WebUI 専用 Virtual Key を初回だけ発行する。"""
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
    """保存keyのLiteLLM状態を返す: valid / missing / blocked / mismatch / error。"""
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
    """ポータルで予約した外部API keyのaliasだけを再発行対象にする。"""
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
    """同じユーザーの外部API key再発行を直列化する。"""
    with _HPC_EXTERNAL_API_KEY_LOCKS_GUARD:
        return _HPC_EXTERNAL_API_KEY_LOCKS.setdefault(username, threading.Lock())


def _hpc_litellm_delete_portal_external_keys(username: str) -> str | None:
    """再発行前にポータル発行の外部API keyをblockして削除・確認する。

    Open WebUI用（source=hpc-portal-openwebui）のkeyには一切触れない。
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
