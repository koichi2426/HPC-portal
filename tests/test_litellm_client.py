"""LiteLLM管理APIクライアントのHTTP境界と秘密情報の秘匿を検証する。"""

import pytest


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


def test_safe_litellm_error_redacts_keys_and_limits_length():
    message = "failed sk-secret_value.more " + "x" * 1000

    safe = portal.llm.gateway.safe_litellm_error(message)

    assert "sk-secret" not in safe
    assert "sk-[REDACTED]" in safe
    assert len(safe) == 500


@pytest.fixture(autouse=True)
def usecase_components(portal_dependencies):
    global client, portal, ollama
    portal = portal_dependencies
    client = portal_dependencies.llm.client
    ollama = portal_dependencies.ollama.backend
