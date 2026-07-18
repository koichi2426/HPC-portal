"""LiteLLM管理APIクライアントのHTTP境界と秘密情報の秘匿を検証する。"""

import io
import json
import urllib.error

import pytest

from hpc_portal.litellm import client


class _FakeResponse:
    """urlopenのcontext managerとして使える最小レスポンス。"""

    def __init__(self, body: bytes):
        """返却するHTTP本文を保持する。

        Args:
            body: readで返すバイト列。
        """
        self.body = body

    def __enter__(self):
        """context managerへ自身を返す。"""
        return self

    def __exit__(self, *_args):
        """例外を抑制せずcontext managerを終了する。"""
        return False

    def read(self):
        """設定されたHTTP本文を返す。"""
        return self.body


def test_litellm_request_builds_authenticated_json_request(monkeypatch):
    observed = {}

    def fake_urlopen(request, timeout):
        """送信予定のリクエストを記録してJSONレスポンスを返す。"""
        observed["request"] = request
        observed["timeout"] = timeout
        return _FakeResponse(b'{"ok":true}')

    monkeypatch.setattr(client.urllib.request, "urlopen", fake_urlopen)

    result = client._hpc_litellm_request("/model/new", {"model_name": "qwen:4b"})

    request = observed["request"]
    assert result == {"ok": True}
    assert request.full_url == "http://127.0.0.1:4000/model/new"
    assert request.method == "POST"
    assert json.loads(request.data) == {"model_name": "qwen:4b"}
    assert request.get_header("Authorization") == "Bearer test-only-master-key"
    assert observed["timeout"] == 15


@pytest.mark.parametrize(("body", "expected"), [(b"", {}), (b"plain text", {"raw": "plain text"})])
def test_litellm_request_handles_empty_and_non_json_response(monkeypatch, body, expected):
    monkeypatch.setattr(
        client.urllib.request, "urlopen", lambda request, timeout: _FakeResponse(body)
    )

    assert client._hpc_litellm_request("/models", method="GET") == expected


def test_litellm_request_reports_bounded_http_error(monkeypatch):
    error = urllib.error.HTTPError(
        "http://127.0.0.1:4000/models",
        500,
        "failed",
        {},
        io.BytesIO(b"x" * 500),
    )
    monkeypatch.setattr(
        client.urllib.request,
        "urlopen",
        lambda request, timeout: (_ for _ in ()).throw(error),
    )

    with pytest.raises(RuntimeError) as exc_info:
        client._hpc_litellm_request("/models", method="GET")

    assert "HTTP 500" in str(exc_info.value)
    assert len(str(exc_info.value)) < 400


def test_litellm_request_reports_connection_error_without_retry(monkeypatch):
    monkeypatch.setattr(
        client.urllib.request,
        "urlopen",
        lambda request, timeout: (_ for _ in ()).throw(
            urllib.error.URLError("connection refused")
        ),
    )

    with pytest.raises(RuntimeError, match="接続失敗"):
        client._hpc_litellm_request("/models", method="GET")


def test_litellm_request_rejects_missing_configuration(monkeypatch):
    monkeypatch.setattr(client, "HPC_LITELLM_MASTER_KEY", "")
    monkeypatch.setattr(
        client.urllib.request, "urlopen", lambda *args, **kwargs: pytest.fail("通信禁止")
    )

    with pytest.raises(RuntimeError, match="未設定"):
        client._hpc_litellm_request("/models", method="GET")


def test_safe_litellm_error_redacts_keys_and_limits_length():
    message = "failed sk-secret_value.more " + "x" * 1000

    safe = client._hpc_safe_litellm_error(message)

    assert "sk-secret" not in safe
    assert "sk-[REDACTED]" in safe
    assert len(safe) == 500

