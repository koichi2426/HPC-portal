"""LiteLLM管理APIのHTTP通信と共通ログ処理を提供する。"""

import json
import re
import urllib.error
import urllib.request

from ..common import (
    HPC_LITELLM_INTERNAL_BASE_URL,
    HPC_LITELLM_LOG,
    HPC_LITELLM_MASTER_KEY,
)

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

def _hpc_safe_litellm_error(error) -> str:
    """ログや画面へ返すエラーから Virtual Key らしい文字列を除去する。

    Args:
        error: 安全化するエラー情報。

    Returns:
        Virtual Keyを除去した画面・ログ用エラー。
    """
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
