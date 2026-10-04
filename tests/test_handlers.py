"""認証後API Handlerの権限、状態変更、HTTP応答を境界から検証する。"""

from types import SimpleNamespace

import pytest
from tornado import web

from hpc_portal.application.usecase import (
    account_management_usecase as user_usecase_module,
)
from hpc_portal.presentation.handlers import admin_users, llm_api, password


class _FakeHandler:
    """Tornado/JupyterHubへ接続せずAPI応答を記録するHandler代替。"""

    def __init__(self, body: bytes, username: str = "admin"):
        """リクエスト本文とログインユーザーを設定する。

        Args:
            body: APIへ渡すJSON本文。
            username: ログイン中として扱うユーザー名。
        """
        self.request = SimpleNamespace(body=body)
        self.current_user = SimpleNamespace(name=username)
        self.authenticator = SimpleNamespace(service="test-pam")
        self.status = 200
        self.headers = {}
        self.response = None

    def set_status(self, status):
        """設定されたHTTPステータスを記録する。"""
        self.status = status

    def set_header(self, name, value):
        """設定されたHTTPヘッダーを記録する。"""
        self.headers[name] = value

    def finish(self, value=None):
        """finishへ渡されたレスポンスを記録する。"""
        self.response = value

    def write(self, value):
        """writeへ渡されたレスポンスを記録する。"""
        self.response = value

    def _require_admin(self):
        """本番Handlerの管理者検証を実行する。"""
        return admin_users.HpcAdminUsersApiHandler._require_admin(self)

    def _api_error(self, status, message):
        """呼び出し元に対応する共通形式でエラーを記録する。"""
        self.set_status(status)
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        self.finish({"error": message})


def test_admin_api_rejects_non_admin_user():
    handler = _FakeHandler(b"{}", username="user01")

    with pytest.raises(web.HTTPError) as exc_info:
        admin_users.HpcAdminUsersApiHandler._require_admin(handler)

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_create_does_not_call_system_for_invalid_username(monkeypatch):
    handler = _FakeHandler(b'{"action":"create","username":"bad;id"}')
    monkeypatch.setattr(
        portal.users.accounts,
        "create_linux_user",
        lambda *args, **kwargs: pytest.fail("OS操作は禁止"),
    )

    await admin_users.HpcAdminUsersApiHandler.post.__wrapped__(handler)

    assert handler.status == 400
    assert "ユーザー名" in handler.response["error"]


@pytest.mark.asyncio
async def test_admin_password_regenerate_rejects_protected_user(monkeypatch):
    handler = _FakeHandler(b'{"action":"password_regenerate","username":"admin"}')
    monkeypatch.setattr(
        portal.users.accounts,
        "set_linux_password",
        lambda *args, **kwargs: pytest.fail("パスワード変更は禁止"),
    )

    await admin_users.HpcAdminUsersApiHandler.post.__wrapped__(handler)

    assert handler.status == 400
    assert "保護されたユーザー" in handler.response["error"]


@pytest.mark.asyncio
async def test_admin_cannot_remove_own_sudo(monkeypatch):
    handler = _FakeHandler(b'{"action":"sudo_disable","username":"admin"}')
    monkeypatch.setattr(
        portal.users.accounts,
        "set_linux_sudo",
        lambda *args, **kwargs: pytest.fail("sudo変更は禁止"),
    )

    await admin_users.HpcAdminUsersApiHandler.post.__wrapped__(handler)

    assert handler.status == 400
    assert "保護されたユーザー" in handler.response["error"]


@pytest.mark.asyncio
async def test_api_disable_attempts_openwebui_stop_even_when_key_update_fails(
    monkeypatch,
):
    handler = _FakeHandler(b'{"action":"api_disable","username":"user01"}')
    stopped = []
    monkeypatch.setattr(
        portal.llm.admin_set_api_access,
        "execute",
        lambda username, enabled: (None, "key update failed"),
    )

    async def fake_stop(username):
        """Open WebUI停止が必ず試行されたことを記録する。"""
        stopped.append(username)
        return "stop failed"

    monkeypatch.setattr(portal.jobs.stop_user_openwebui_servers, "execute", fake_stop)

    await admin_users.HpcAdminUsersApiHandler.post.__wrapped__(handler)

    assert stopped == ["user01"]
    assert handler.status == 400
    assert handler.response["error"] == "key update failed; stop failed"


@pytest.mark.asyncio
async def test_ollama_delete_restores_litellm_registration_when_backend_delete_fails(
    monkeypatch,
):
    handler = _FakeHandler(b'{"action":"ollama_delete","model":"qwen:4b"}')
    registered = []
    delete_calls = []

    def fake_ollama(action, model=None, *_args):
        """tag一覧はモデルあり、deleteだけ失敗として返す。"""
        if action == "tags":
            return {"models": [{"name": "qwen:4b"}]}, None
        if action == "delete":
            delete_calls.append(model)
            return None, "delete failed"
        raise AssertionError(action)

    monkeypatch.setattr(portal.ollama.backend, "command", fake_ollama)
    monkeypatch.setattr(portal.llm.delete_ollama_model, "execute", lambda model: None)
    monkeypatch.setattr(
        portal.llm.register_ollama_model,
        "execute",
        lambda model: registered.append(model) or ({"state": "registered"}, None),
    )

    await admin_users.HpcAdminUsersApiHandler.post.__wrapped__(handler)

    assert delete_calls == ["qwen:4b"]
    assert registered == ["qwen:4b"]
    assert handler.status == 400
    assert handler.response == {"error": "delete failed"}


@pytest.mark.asyncio
async def test_password_change_updates_only_logged_in_user_after_verification(
    monkeypatch,
):
    handler = _FakeHandler(
        b'{"current_password":"OldPass12","new_password":"NewPass34","confirm_password":"NewPass34"}',
        username="user01",
    )
    calls = []
    monkeypatch.setattr(user_usecase_module, "validate_password", lambda value: None)
    monkeypatch.setattr(
        portal.users.accounts,
        "verify_linux_password",
        lambda username, value, service: calls.append(
            ("verify", username, value, service)
        ),
    )
    monkeypatch.setattr(
        portal.users.accounts,
        "set_linux_password",
        lambda username, value: calls.append(("set", username, value)),
    )
    monkeypatch.setattr(
        user_usecase_module,
        "log_password_success",
        lambda actor, target: calls.append(("log", actor, target)),
    )

    await password.HpcPasswordApiHandler.post.__wrapped__(handler)

    assert handler.response == {"ok": True}
    assert calls == [
        ("verify", "user01", "OldPass12", "test-pam"),
        ("set", "user01", "NewPass34"),
        ("log", "user01", "user01"),
    ]


@pytest.mark.asyncio
async def test_password_change_rejects_mismatch_before_pam(monkeypatch):
    handler = _FakeHandler(
        b'{"current_password":"OldPass12","new_password":"NewPass34","confirm_password":"Different56"}',
        username="user01",
    )
    monkeypatch.setattr(
        portal.users.accounts,
        "verify_linux_password",
        lambda *args, **kwargs: pytest.fail("PAM呼び出しは禁止"),
    )

    await password.HpcPasswordApiHandler.post.__wrapped__(handler)

    assert handler.status == 400
    assert "一致しません" in handler.response["error"]


@pytest.mark.asyncio
async def test_llm_api_regenerates_key_only_for_logged_in_user(monkeypatch):
    handler = _FakeHandler(b'{"action":"regenerate"}', username="user01")
    usernames = []
    monkeypatch.setattr(
        portal.llm.regenerate_own_key,
        "execute",
        lambda username: usernames.append(username) or ("sk-new-key", None),
    )
    monkeypatch.setattr(
        portal.users.settings,
        "llm_public_base_url",
        "https://llm.example.test/v1",
    )

    await llm_api.HpcLlmApiApiHandler.post.__wrapped__(handler)

    assert usernames == ["user01"]
    assert handler.response == {
        "ok": True,
        "api_key": "sk-new-key",
        "api_base_url": "https://llm.example.test/v1",
    }


@pytest.fixture(autouse=True)
def usecase_components(portal_dependencies):
    global portal
    portal = portal_dependencies
