"""検索結果URLを改ざん検知付きの短寿命参照へ変換する。"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import time
from typing import Any


FETCH_REFERENCE_TTL = int(os.environ.get("SEARCH_FETCH_REFERENCE_TTL", "300"))
FETCH_REFERENCE_MAX_LENGTH = int(
    os.environ.get("SEARCH_FETCH_REFERENCE_MAX_LENGTH", "4096")
)
_KEY_CONTEXT = b"hpc-search-fetch-reference-v1"


class FetchReferenceError(ValueError):
    """署名付き参照が不正または期限切れであることを示す。"""


def _secret_key() -> bytes:
    """環境変数から署名鍵を導出する。

    Returns:
        用途を分離したHMAC署名鍵。

    Raises:
        RuntimeError: 内部認証tokenが未設定または短すぎる場合。
    """
    secret = os.environ.get("SEARCH_MCP_AUTH_TOKEN", "")
    if len(secret) < 32:
        raise RuntimeError("SEARCH_MCP_AUTH_TOKEN must contain at least 32 characters")
    return hmac.new(secret.encode(), _KEY_CONTEXT, hashlib.sha256).digest()


def _encode(value: bytes) -> str:
    """バイト列をパディングなしURL-safe Base64へ変換する。"""
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    """パディングなしURL-safe Base64を復号する。

    Args:
        value: Base64文字列。

    Returns:
        復号したバイト列。

    Raises:
        FetchReferenceError: Base64が不正な場合。
    """
    try:
        return base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, TypeError, binascii.Error) as exc:
        raise FetchReferenceError("検索結果の参照が不正です") from exc


def create_fetch_reference(url: str, *, now: int | None = None) -> str:
    """検索結果URLの署名付き短寿命参照を作成する。

    Args:
        url: SearXNGが返した公開WebページURL。
        now: テスト用のUNIX時刻。省略時は現在時刻。

    Returns:
        改ざん検知付き参照文字列。
    """
    payload = json.dumps(
        {"v": 1, "u": str(url), "iat": int(time.time() if now is None else now)},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    encoded_payload = _encode(payload)
    signature = hmac.new(_secret_key(), encoded_payload.encode(), hashlib.sha256).digest()
    return f"{encoded_payload}.{_encode(signature)}"


def verify_fetch_reference(reference: str, *, now: int | None = None) -> str:
    """署名と有効期限を検証して元URLを返す。

    Args:
        reference: search_webが返した署名付き参照。
        now: テスト用のUNIX時刻。省略時は現在時刻。

    Returns:
        署名済みの元URL。

    Raises:
        FetchReferenceError: 参照が不正、改ざん済み、期限切れの場合。
    """
    value = str(reference).strip()
    if not value or len(value) > FETCH_REFERENCE_MAX_LENGTH or value.count(".") != 1:
        raise FetchReferenceError("検索結果の参照が不正です")
    encoded_payload, encoded_signature = value.split(".", 1)
    expected = hmac.new(_secret_key(), encoded_payload.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(_decode(encoded_signature), expected):
        raise FetchReferenceError("検索結果の参照が改ざんされています")
    try:
        payload: Any = json.loads(_decode(encoded_payload))
        issued_at = int(payload["iat"])
        url = payload["u"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise FetchReferenceError("検索結果の参照が不正です") from exc
    current = int(time.time() if now is None else now)
    if (
        not isinstance(payload, dict)
        or payload.get("v") != 1
        or not isinstance(url, str)
        or not url
        or issued_at > current + 30
        or current - issued_at > FETCH_REFERENCE_TTL
    ):
        raise FetchReferenceError("検索結果の参照が期限切れか不正です")
    return url
