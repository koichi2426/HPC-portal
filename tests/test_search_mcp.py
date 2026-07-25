"""SearXNG検索MCPの検索・安全なWeb本文取得を検証する。"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
from pathlib import Path

import pytest


SEARCH_MCP_FILES = (
    Path(__file__).resolve().parents[1] / "roles" / "search_mcp" / "files"
)
sys.path.insert(0, str(SEARCH_MCP_FILES))
os.environ.setdefault("SEARCH_MCP_AUTH_TOKEN", "t" * 64)

import combined_search  # noqa: E402
import fetch_reference  # noqa: E402
import mcp_auth  # noqa: E402
import search_service  # noqa: E402
import web_fetch  # noqa: E402


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
    assert set(result["results"][0]) == {
        "title",
        "url",
        "snippet",
        "engine",
        "fetch_ref",
    }
    assert fetch_reference.verify_fetch_reference(
        result["results"][0]["fetch_ref"]
    ) == result["results"][0]["url"]
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


def _install_dns_result(monkeypatch, addresses):
    """dnspythonへテスト用の名前解決結果を設定する。

    Args:
        monkeypatch: pytestの差し替え機能。
        addresses: 名前解決結果として返すIPアドレス。
    """
    class FakeResolver:
        """A・AAAAレコードをメモリ上から返すResolver。"""

        def resolve(self, _hostname, record_type, **_kwargs):
            """要求された種類のIPだけを返す。"""
            selected = [
                address
                for address in addresses
                if (":" in address) == (record_type == "AAAA")
            ]
            if not selected:
                raise web_fetch.dns.resolver.NoAnswer
            return selected

    monkeypatch.setattr(web_fetch.dns.resolver, "Resolver", FakeResolver)


def _fetch_ref(url):
    """現在有効なテスト用署名付き参照を作成する。"""
    return fetch_reference.create_fetch_reference(url)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "224.0.0.1",
        "::1",
        "fc00::1",
        "fe80::1",
        "::ffff:127.0.0.1",
    ],
)
def test_fetch_web_page_rejects_non_public_dns_addresses(monkeypatch, address):
    """名前解決先が内部・予約アドレスの場合に取得を拒否する。"""
    _install_dns_result(monkeypatch, [address])

    with pytest.raises(web_fetch.WebFetchError, match="非公開アドレス"):
        web_fetch._validate_and_resolve_url(
            "https://example.com/page", time.monotonic() + 10
        )


def test_fetch_web_page_rejects_mixed_public_and_private_dns(monkeypatch):
    """公開IPと内部IPが混在するホストを安全側で拒否する。"""
    _install_dns_result(monkeypatch, ["93.184.216.34", "127.0.0.1"])

    with pytest.raises(web_fetch.WebFetchError, match="非公開アドレス"):
        web_fetch._validate_and_resolve_url(
            "https://example.com/", time.monotonic() + 10
        )


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/file",
        "http://user:password@example.com/",
        "https://example.com:8443/",
        "http://localhost/",
    ],
)
def test_fetch_web_page_rejects_unsafe_url_forms(url):
    """危険なスキーム、認証情報、ポート、localhostを拒否する。"""
    with pytest.raises(web_fetch.WebFetchError):
        web_fetch._validate_and_resolve_url(url, time.monotonic() + 10)


def test_fetch_web_page_extracts_html_and_marks_untrusted_content(monkeypatch):
    """公開HTMLから不要要素を除外し、未信頼テキストとして返す。"""
    monkeypatch.setattr(
        web_fetch,
        "_resolve_public_addresses",
        lambda *_args: ("93.184.216.34",),
    )
    monkeypatch.setattr(
        web_fetch,
        "_request_once",
        lambda _target, _deadline: web_fetch._HttpResponse(
            status=200,
            headers={"content-type": "text/html; charset=utf-8"},
            body=(
                b"<html><head><title>Example Page</title>"
                b"<script>steal_secret()</script></head>"
                b"<body><nav>menu</nav><main><h1>Heading</h1>"
                b"<p>Useful content.</p></main><footer>footer</footer></body></html>"
            ),
        ),
    )

    result = web_fetch.fetch_web_page(
        _fetch_ref("https://example.com/article#section")
    )

    assert result["url"] == "https://example.com/article"
    assert result["title"] == "Example Page"
    assert result["content"] == "Heading\nUseful content."
    assert result["content_type"] == "text/html"
    assert result["truncated"] is False
    assert "未信頼テキスト" in result["security_notice"]
    assert "steal_secret" not in result["content"]
    assert "menu" not in result["content"]


def test_fetch_web_page_revalidates_redirect_and_blocks_private_target(monkeypatch):
    """外部URLからlocalhostへ向かうリダイレクトを拒否する。"""
    def resolve_addresses(hostname, _port, _deadline):
        if hostname == "127.0.0.1":
            raise web_fetch.WebFetchError(
                "内部ネットワークまたは非公開アドレスには接続できません"
            )
        return ("93.184.216.34",)

    monkeypatch.setattr(
        web_fetch,
        "_resolve_public_addresses",
        resolve_addresses,
    )
    monkeypatch.setattr(
        web_fetch,
        "_request_once",
        lambda _target, _deadline: web_fetch._HttpResponse(
            status=302,
            headers={"location": "http://127.0.0.1/config"},
            body=b"",
        ),
    )

    with pytest.raises(web_fetch.WebFetchError, match="非公開アドレス"):
        web_fetch.fetch_web_page(_fetch_ref("https://example.com/redirect"))


@pytest.mark.parametrize(
    ("headers", "message"),
    [
        ({"content-type": "application/pdf"}, "HTMLまたはプレーンテキスト"),
        (
            {"content-type": "text/html", "content-encoding": "gzip"},
            "圧縮されたWebページ",
        ),
    ],
)
def test_fetch_web_page_rejects_unsupported_responses(monkeypatch, headers, message):
    """非テキストと意図しない圧縮応答を拒否する。"""
    monkeypatch.setattr(
        web_fetch,
        "_resolve_public_addresses",
        lambda *_args: ("93.184.216.34",),
    )
    monkeypatch.setattr(
        web_fetch,
        "_request_once",
        lambda _target, _deadline: web_fetch._HttpResponse(
            status=200,
            headers=headers,
            body=b"content",
        ),
    )

    with pytest.raises(web_fetch.WebFetchError, match=message):
        web_fetch.fetch_web_page(_fetch_ref("https://example.com/file"))


def test_fetch_web_page_truncates_extracted_content(monkeypatch):
    """抽出本文を設定文字数で切り詰める。"""
    monkeypatch.setattr(web_fetch, "FETCH_MAX_CONTENT_LENGTH", 12)
    monkeypatch.setattr(
        web_fetch,
        "_resolve_public_addresses",
        lambda *_args: ("93.184.216.34",),
    )
    monkeypatch.setattr(
        web_fetch,
        "_request_once",
        lambda _target, _deadline: web_fetch._HttpResponse(
            status=200,
            headers={"content-type": "text/plain; charset=utf-8"},
            body="これは十分に長い本文です。さらに文章が続きます。".encode(),
        ),
    )

    result = web_fetch.fetch_web_page(_fetch_ref("https://example.com/long.txt"))

    assert len(result["content"]) == 12
    assert result["truncated"] is True


def test_request_once_rejects_body_over_byte_limit(monkeypatch):
    """Content-Lengthがなくても読み取り上限を超えた応答を拒否する。"""
    class OversizedResponse:
        """上限超過のHTTP応答を返すテスト用オブジェクト。"""

        status = 200

        def getheaders(self):
            """最小限のContent-Typeヘッダーを返す。"""
            return [("Content-Type", "text/html")]

        def read1(self, size):
            """要求サイズいっぱいの上限超過本文を返す。

            Args:
                size: 呼び出し側が要求した最大読み取りサイズ。

            Returns:
                指定サイズのバイト列。
            """
            return b"x" * size

        def close(self):
            """テスト用HTTP応答を閉じる。"""

    class FakeConnection:
        """ネットワーク通信を行わないHTTP接続。"""

        def __init__(self, *_args, **_kwargs):
            self.sock = None

        def request(self, *_args, **_kwargs):
            """HTTP要求を受け入れる。"""

        def getresponse(self):
            """上限超過応答を返す。"""
            return OversizedResponse()

        def close(self):
            """テスト用接続を閉じる。"""

    monkeypatch.setattr(web_fetch.http.client, "HTTPSConnection", FakeConnection)

    class FakeSocket:
        """タイムアウト設定だけを受け付けるテスト用ソケット。"""

        def settimeout(self, _timeout):
            """指定されたタイムアウトを受け付ける。"""

    monkeypatch.setattr(
        web_fetch, "_open_pinned_socket", lambda _target, _deadline: FakeSocket()
    )
    target = web_fetch._ResolvedTarget(
        url="https://example.com/",
        scheme="https",
        hostname="example.com",
        port=443,
        request_target="/",
        addresses=("93.184.216.34",),
    )

    with pytest.raises(web_fetch.WebFetchError, match="応答が大きすぎます"):
        web_fetch._request_once(target, time.monotonic() + 10)


def test_read_response_body_enforces_total_timeout(monkeypatch):
    """データが少しずつ届いても本文取得全体の期限で停止する。"""
    class SlowResponse:
        """本文を完了させないテスト用HTTP応答。"""

        def read1(self, _size):
            """1バイトだけ返して接続を継続する。"""
            return b"x"

    class FakeSocket:
        """設定されたタイムアウトを記録するテスト用ソケット。"""

        def __init__(self):
            self.timeouts = []

        def settimeout(self, timeout):
            """タイムアウト値を記録する。

            Args:
                timeout: 次の読み取りへ適用する残り時間。
            """
            self.timeouts.append(timeout)

    timestamps = iter([100.1, 109.0])
    monkeypatch.setattr(web_fetch.time, "monotonic", lambda: next(timestamps))
    fake_socket = FakeSocket()

    with pytest.raises(web_fetch.WebFetchError, match="タイムアウト"):
        web_fetch._read_response_body(SlowResponse(), fake_socket, 108.0)

    assert fake_socket.timeouts == [pytest.approx(7.9)]


def test_open_pinned_socket_connects_to_validated_address(monkeypatch):
    """HTTP接続がホスト名を再解決せず検証済みIPを使用する。"""
    captured = {}
    fake_socket = object()

    def fake_create_connection(address, timeout):
        captured["address"] = address
        captured["timeout"] = timeout
        return fake_socket

    monkeypatch.setattr(web_fetch.socket, "create_connection", fake_create_connection)
    target = web_fetch._ResolvedTarget(
        url="http://example.com/",
        scheme="http",
        hostname="example.com",
        port=80,
        request_target="/",
        addresses=("93.184.216.34",),
    )

    result = web_fetch._open_pinned_socket(target, time.monotonic() + 10)

    assert result is fake_socket
    assert captured["address"] == ("93.184.216.34", 80)
    assert captured["timeout"] == pytest.approx(web_fetch.FETCH_CONNECT_TIMEOUT)


def test_fetch_reference_rejects_tampering_and_expiration(monkeypatch):
    """検索結果参照の改ざんと期限切れを拒否する。"""
    monkeypatch.setattr(fetch_reference, "FETCH_REFERENCE_TTL", 300)
    reference = fetch_reference.create_fetch_reference(
        "https://example.com/article", now=1000
    )

    replacement = "A" if reference[0] != "A" else "B"
    with pytest.raises(fetch_reference.FetchReferenceError, match="改ざん"):
        fetch_reference.verify_fetch_reference(replacement + reference[1:], now=1001)
    with pytest.raises(fetch_reference.FetchReferenceError, match="期限切れ"):
        fetch_reference.verify_fetch_reference(reference, now=1301)


def test_fetch_web_page_rejects_raw_url():
    """search_webを経由していない任意URLを本文取得へ渡せないことを確認する。"""
    with pytest.raises(web_fetch.WebFetchError, match="参照が不正"):
        web_fetch.fetch_web_page("https://example.com/private-probe")


def test_internal_bearer_token_uses_constant_time_comparison():
    """内部Bearer tokenが完全一致するときだけ認証されることを確認する。"""
    expected = "a" * 64

    assert mcp_auth.validate_bearer_token(expected, expected) is True
    assert mcp_auth.validate_bearer_token("b" * 64, expected) is False
    assert mcp_auth.validate_bearer_token("", expected) is False


def test_dns_result_count_is_limited_after_private_address_validation(monkeypatch):
    """多数の公開IPが返っても接続試行数を設定上限へ制限する。"""
    addresses = [f"93.184.216.{number}" for number in range(10, 20)]
    _install_dns_result(monkeypatch, addresses)
    monkeypatch.setattr(web_fetch, "FETCH_MAX_ADDRESSES", 4)

    result = web_fetch._resolve_public_addresses(
        "example.com", 443, time.monotonic() + 10
    )

    assert result == tuple(addresses[:4])


def test_redirects_share_one_fetch_deadline(monkeypatch):
    """リダイレクトごとに取得期限が延長されないことを確認する。"""
    deadlines = []
    urls = []

    def fake_validate(url, deadline):
        urls.append(url)
        deadlines.append(deadline)
        return web_fetch._ResolvedTarget(
            url=url,
            scheme="https",
            hostname="example.com",
            port=443,
            request_target="/",
            addresses=("93.184.216.34",),
        )

    responses = iter(
        [
            web_fetch._HttpResponse(
                status=302,
                headers={"location": "https://example.com/final"},
                body=b"",
            ),
            web_fetch._HttpResponse(
                status=200,
                headers={"content-type": "text/plain"},
                body=b"final content",
            ),
        ]
    )

    def fake_request(_target, deadline):
        deadlines.append(deadline)
        return next(responses)

    monkeypatch.setattr(web_fetch, "_validate_and_resolve_url", fake_validate)
    monkeypatch.setattr(web_fetch, "_request_once", fake_request)

    result = web_fetch.fetch_web_page(_fetch_ref("https://example.com/start"))

    assert result["content"] == "final content"
    assert urls == ["https://example.com/start", "https://example.com/final"]
    assert len(set(deadlines)) == 1


def test_search_and_fetch_web_returns_page_bodies_in_one_call(monkeypatch):
    """統合検索が検索と複数ページの本文取得を1回で完了することを確認する。"""
    candidates = [
        {
            "title": f"Result {index}",
            "url": f"https://example.com/{index}",
            "snippet": f"Snippet {index}",
            "fetch_ref": f"reference-{index}",
        }
        for index in range(3)
    ]
    monkeypatch.setattr(
        combined_search,
        "search_web",
        lambda **_kwargs: {
            "query": "latest model",
            "results": candidates,
            "unresponsive_engines": [],
        },
    )
    deadlines = []

    def fake_fetch(reference, *, deadline):
        deadlines.append(deadline)
        index = reference.rsplit("-", 1)[1]
        return {
            "title": f"Page {index}",
            "url": f"https://example.com/{index}",
            "content": f"Body {index}",
            "content_type": "text/html",
            "truncated": False,
        }

    monkeypatch.setattr(combined_search, "fetch_web_page", fake_fetch)

    result = combined_search.search_and_fetch_web("latest model", count=2)

    assert [page["content"] for page in result["pages"]] == ["Body 0", "Body 1"]
    assert result["fetch_failures"] == []
    assert len(set(deadlines)) == 1
    assert "未信頼テキスト" in result["security_notice"]


def test_search_and_fetch_web_skips_failed_candidate(monkeypatch):
    """取得できない候補を記録し、次の検索結果から本文を取得する。"""
    monkeypatch.setattr(
        combined_search,
        "search_web",
        lambda **_kwargs: {
            "query": "query",
            "results": [
                {
                    "title": "Blocked",
                    "url": "https://blocked.example/",
                    "snippet": "",
                    "fetch_ref": "blocked",
                },
                {
                    "title": "Available",
                    "url": "https://example.com/",
                    "snippet": "summary",
                    "fetch_ref": "available",
                },
            ],
            "unresponsive_engines": [],
        },
    )

    def fake_fetch(reference, *, deadline):
        del deadline
        if reference == "blocked":
            raise web_fetch.WebFetchError("取得を拒否しました")
        return {
            "title": "Available",
            "url": "https://example.com/",
            "content": "Page body",
            "content_type": "text/plain",
            "truncated": False,
        }

    monkeypatch.setattr(combined_search, "fetch_web_page", fake_fetch)

    result = combined_search.search_and_fetch_web("query", count=1)

    assert result["pages"][0]["content"] == "Page body"
    assert result["fetch_failures"] == [
        {"url": "https://blocked.example/", "error": "取得を拒否しました"}
    ]


def test_search_and_fetch_web_rejects_when_all_pages_fail(monkeypatch):
    """検索候補の本文を1件も取得できない場合は安全なエラーを返す。"""
    monkeypatch.setattr(
        combined_search,
        "search_web",
        lambda **_kwargs: {
            "query": "query",
            "results": [
                {
                    "title": "Blocked",
                    "url": "https://blocked.example/",
                    "snippet": "",
                    "fetch_ref": "blocked",
                }
            ],
            "unresponsive_engines": [],
        },
    )
    monkeypatch.setattr(
        combined_search,
        "fetch_web_page",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            web_fetch.WebFetchError("取得を拒否しました")
        ),
    )

    with pytest.raises(
        combined_search.CombinedSearchError,
        match="本文を取得できませんでした",
    ):
        combined_search.search_and_fetch_web("query")
