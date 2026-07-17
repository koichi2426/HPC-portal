"""SearXNG検索結果をMCP向けの小さなJSONへ変換する。"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


SEARXNG_SEARCH_URL = os.environ.get(
    "SEARXNG_SEARCH_URL", "http://127.0.0.1:8888/search"
)
DEFAULT_RESULT_COUNT = int(os.environ.get("SEARCH_DEFAULT_RESULT_COUNT", "6"))
MAX_RESULT_COUNT = int(os.environ.get("SEARCH_MAX_RESULT_COUNT", "10"))
QUERY_MAX_LENGTH = int(os.environ.get("SEARCH_QUERY_MAX_LENGTH", "256"))
SEARCH_TIMEOUT = float(os.environ.get("SEARCH_TIMEOUT", "5"))
MAX_RESPONSE_BYTES = int(os.environ.get("SEARCH_MAX_RESPONSE_BYTES", "2097152"))


class SearchServiceError(RuntimeError):
    """利用者へ安全に返せるWeb検索エラー。"""


def _normalized_count(count: int) -> int:
    """検索件数を1件以上かつ設定上限以下に丸める。

    Args:
        count: 利用者が要求した検索件数。

    Returns:
        設定範囲内へ丸めた検索件数。
    """
    return max(1, min(int(count), MAX_RESULT_COUNT))


def _result_item(item: Any) -> dict[str, str] | None:
    """SearXNGの1件を公開用フィールドへ絞り込む。

    Args:
        item: SearXNGが返した検索結果。

    Returns:
        正規化済み結果。不正なURLの場合はNone。
    """
    if not isinstance(item, dict):
        return None
    url = str(item.get("url") or "").strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    engines = item.get("engines") or []
    if isinstance(engines, list):
        engine = ", ".join(str(value) for value in engines if value)
    else:
        engine = str(item.get("engine") or "")
    return {
        "title": str(item.get("title") or "").strip(),
        "url": url,
        "snippet": str(item.get("content") or "").strip(),
        "engine": engine,
    }


def search_web(
    query: str,
    count: int = DEFAULT_RESULT_COUNT,
    language: str = "ja-JP",
) -> dict[str, Any]:
    """SearXNGでWeb検索し、タイトル・URL・概要を返す。

    Args:
        query: 検索語。最大文字数は環境設定で制限する。
        count: 返す検索結果数。設定上限を超えた値は上限へ丸める。
        language: SearXNGへ渡す検索言語。

    Returns:
        検索語、検索結果、応答しなかった検索エンジン。

    Raises:
        SearchServiceError: 検索語が不正、応答過大、通信失敗の場合。
    """
    normalized_query = str(query).strip()
    if not normalized_query:
        raise SearchServiceError("検索語を入力してください")
    if len(normalized_query) > QUERY_MAX_LENGTH:
        raise SearchServiceError(f"検索語は{QUERY_MAX_LENGTH}文字以内にしてください")

    result_count = _normalized_count(count)
    params = urllib.parse.urlencode(
        {
            "q": normalized_query,
            "format": "json",
            "language": str(language).strip() or "all",
            "safesearch": "1",
        }
    )
    request = urllib.request.Request(
        f"{SEARXNG_SEARCH_URL}?{params}",
        headers={
            "Accept": "application/json",
            "User-Agent": "HPC-Portal-Search-MCP/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=SEARCH_TIMEOUT) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SearchServiceError("Web検索サービスへ接続できませんでした") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise SearchServiceError("Web検索サービスの応答が大きすぎます")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SearchServiceError("Web検索サービスの応答を解析できませんでした") from exc
    if not isinstance(payload, dict):
        raise SearchServiceError("Web検索サービスの応答形式が不正です")

    results = []
    for item in payload.get("results") or []:
        normalized = _result_item(item)
        if normalized is not None:
            results.append(normalized)
        if len(results) >= result_count:
            break
    return {
        "query": normalized_query,
        "results": results,
        "unresponsive_engines": payload.get("unresponsive_engines") or [],
    }
