"""SearXNG検索MCPの入力制限と応答変換を検証する。"""

import json
import sys
import urllib.error
import urllib.parse
from pathlib import Path

import pytest


SEARCH_MCP_FILES = (
    Path(__file__).resolve().parents[1] / "roles" / "search_mcp" / "files"
)
sys.path.insert(0, str(SEARCH_MCP_FILES))

import search_service  # noqa: E402


class FakeResponse:
    """urllibのレスポンスとして使う最小コンテキストマネージャー。"""

    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size):
        return self.payload


def test_search_web_returns_public_fields_and_caps_result_count(monkeypatch):
    """公開項目だけを返し、要求件数を設定上限へ丸めることを確認する。"""
    captured = {}
    payload = {
        "results": [
            {
                "title": f"結果{i}",
                "url": f"https://example.com/{i}",
                "content": f"概要{i}",
                "engines": ["duckduckgo"],
                "raw_secret": "公開しない値",
            }
            for i in range(12)
        ],
        "unresponsive_engines": [],
    }

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return FakeResponse(payload)

    monkeypatch.setattr(search_service.urllib.request, "urlopen", fake_urlopen)

    result = search_service.search_web("HPC portal", count=999)

    assert len(result["results"]) == search_service.MAX_RESULT_COUNT
    assert set(result["results"][0]) == {"title", "url", "snippet", "engine"}
    assert urllib.parse.parse_qs(urllib.parse.urlparse(captured["url"]).query)["q"] == [
        "HPC portal"
    ]
    assert captured["timeout"] == search_service.SEARCH_TIMEOUT


def test_search_web_rejects_empty_and_too_long_queries():
    """空または上限超過の検索語をSearXNGへ送らないことを確認する。"""
    with pytest.raises(search_service.SearchServiceError):
        search_service.search_web("   ")
    with pytest.raises(search_service.SearchServiceError):
        search_service.search_web("x" * (search_service.QUERY_MAX_LENGTH + 1))


def test_search_web_returns_safe_error_when_searxng_is_unavailable(monkeypatch):
    """SearXNG障害時に内部例外をそのまま公開しないことを確認する。"""
    monkeypatch.setattr(
        search_service.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            urllib.error.URLError("connection refused")
        ),
    )

    with pytest.raises(
        search_service.SearchServiceError,
        match="Web検索サービスへ接続できませんでした",
    ):
        search_service.search_web("SearXNG")
