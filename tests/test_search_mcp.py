"""SearXNG検索MCPの検索・安全なWeb本文取得を検証する。"""

import json
import os
import time

import pytest

from hpc_search_mcp import (
    fetch_reference,  # noqa: E402
    mcp_auth,  # noqa: E402
    web_fetch,  # noqa: E402
)

os.environ.setdefault("SEARCH_MCP_AUTH_TOKEN", "t" * 64)


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


def test_internal_bearer_token_uses_constant_time_comparison():
    """内部Bearer tokenが完全一致するときだけ認証されることを確認する。"""
    expected = "a" * 64

    assert mcp_auth.validate_bearer_token(expected, expected) is True
    assert mcp_auth.validate_bearer_token("b" * 64, expected) is False
    assert mcp_auth.validate_bearer_token("", expected) is False
