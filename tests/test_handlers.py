"""認証後API Handlerの権限、状態変更、HTTP応答を境界から検証する。"""

from types import SimpleNamespace

import pytest
from tornado import web

from hpc_portal.handlers import admin_users, llm_api, password


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
async def test_admin_create_returns_password_key_and_created_status(monkeypatch):
    handler = _FakeHandler(
        '{"action":"create","username":"user01","display_name":"研究 太郎","sudo":false}'.encode()
    )
    created = []
    monkeypatch.setattr(admin_users, "_hpc_generate_password", lambda: "RandomPass12")
    monkeypatch.setattr(
        admin_users,
        "_hpc_create_linux_user",
        lambda username, generated, sudo, display_name: created.append(
            (username, generated, sudo, display_name)
        ),
    )
    monkeypatch.setattr(
        admin_users,
        "_hpc_litellm_generate_key",
        lambda username: ("sk-test-user-key", None),
    )
    monkeypatch.setattr(admin_users, "HPC_LITELLM_PUBLIC_BASE_URL", "https://llm.example.test/v1")

    await admin_users.HpcAdminUsersApiHandler.post.__wrapped__(handler)

    assert handler.status == 201
    assert handler.headers["Cache-Control"] == "no-store"
    assert handler.response == {
        "ok": True,
        "username": "user01",
        "initial_password": "RandomPass12",
        "api_key": "sk-test-user-key",
        "api_base_url": "https://llm.example.test/v1",
    }
    assert created == [("user01", "RandomPass12", False, "研究 太郎")]


@pytest.mark.asyncio
async def test_admin_create_does_not_call_system_for_invalid_username(monkeypatch):
    handler = _FakeHandler(b'{"action":"create","username":"bad;id"}')
    monkeypatch.setattr(
        admin_users,
        "_hpc_create_linux_user",
        lambda *args, **kwargs: pytest.fail("OS操作は禁止"),
    )

    await admin_users.HpcAdminUsersApiHandler.post.__wrapped__(handler)

    assert handler.status == 400
    assert "ユーザー名" in handler.response["error"]


@pytest.mark.asyncio
async def test_admin_password_regenerate_rejects_protected_user(monkeypatch):
    handler = _FakeHandler(b'{"action":"password_regenerate","username":"admin"}')
    monkeypatch.setattr(
        admin_users,
        "_hpc_set_linux_password",
        lambda *args, **kwargs: pytest.fail("パスワード変更は禁止"),
    )

    await admin_users.HpcAdminUsersApiHandler.post.__wrapped__(handler)

    assert handler.status == 400
    assert "保護されたユーザー" in handler.response["error"]


@pytest.mark.asyncio
async def test_admin_cannot_remove_own_sudo(monkeypatch):
    handler = _FakeHandler(b'{"action":"sudo_disable","username":"admin"}')
    monkeypatch.setattr(
        admin_users,
        "_hpc_set_linux_sudo",
        lambda *args, **kwargs: pytest.fail("sudo変更は禁止"),
    )

    await admin_users.HpcAdminUsersApiHandler.post.__wrapped__(handler)

    assert handler.status == 400
    assert "保護されたユーザー" in handler.response["error"]


@pytest.mark.asyncio
async def test_api_disable_attempts_openwebui_stop_even_when_key_update_fails(monkeypatch):
    handler = _FakeHandler(b'{"action":"api_disable","username":"user01"}')
    stopped = []
    monkeypatch.setattr(
        admin_users,
        "_hpc_litellm_admin_set_api_access",
        lambda username, enabled: (None, "key update failed"),
    )

    async def fake_stop(current_handler, username):
        """Open WebUI停止が必ず試行されたことを記録する。"""
        stopped.append((current_handler, username))
        return "stop failed"

    monkeypatch.setattr(admin_users, "_hpc_stop_user_openwebui_servers", fake_stop)

    await admin_users.HpcAdminUsersApiHandler.post.__wrapped__(handler)

    assert stopped == [(handler, "user01")]
    assert handler.status == 400
    assert handler.response["error"] == "key update failed; stop failed"


@pytest.mark.asyncio
async def test_ollama_delete_restores_litellm_registration_when_backend_delete_fails(monkeypatch):
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

    monkeypatch.setattr(admin_users, "_hpc_ollama_cmd", fake_ollama)
    monkeypatch.setattr(admin_users, "_hpc_litellm_delete_ollama_model", lambda model: None)
    monkeypatch.setattr(
        admin_users,
        "_hpc_litellm_register_ollama_model",
        lambda model: registered.append(model) or ({"state": "registered"}, None),
    )

    await admin_users.HpcAdminUsersApiHandler.post.__wrapped__(handler)

    assert delete_calls == ["qwen:4b"]
    assert registered == ["qwen:4b"]
    assert handler.status == 400
    assert handler.response == {"error": "delete failed"}


@pytest.mark.asyncio
async def test_ollama_sync_models_returns_sync_summary(monkeypatch):
    handler = _FakeHandler(b'{"action":"ollama_sync_models"}')
    summary = {"total": 3, "changed": 2, "failed": 0, "results": []}
    monkeypatch.setattr(
        admin_users,
        "_hpc_litellm_sync_ollama_models",
        lambda: (summary, None),
    )

    await admin_users.HpcAdminUsersApiHandler.post.__wrapped__(handler)

    assert handler.response == {"ok": True, "data": summary}


@pytest.mark.asyncio
async def test_ollama_update_runs_fixed_management_action(monkeypatch):
    """管理APIの更新操作が固定のupdateコマンドだけを呼ぶことを確認する。"""
    handler = _FakeHandler(b'{"action":"ollama_update"}')
    calls = []
    monkeypatch.setattr(
        admin_users,
        "_hpc_ollama_cmd",
        lambda action, *args: calls.append((action, args))
        or ({"status": "started", "job_ids": "43"}, None),
    )

    await admin_users.HpcAdminUsersApiHandler.post.__wrapped__(handler)

    assert calls == [("update", (None, None, None, None, None, None, None, None, None, None))]
    assert handler.response == {
        "ok": True,
        "data": {"status": "started", "job_ids": "43"},
    }


@pytest.mark.asyncio
async def test_ollama_update_check_runs_server_side_latest_lookup(monkeypatch):
    handler = _FakeHandler(b'{"action":"ollama_update_check"}')
    calls = []
    monkeypatch.setattr(
        admin_users,
        "_hpc_ollama_cmd",
        lambda action, *args: calls.append((action, args))
        or ({"latest_version": "0.33.0", "update_available": True}, None),
    )

    await admin_users.HpcAdminUsersApiHandler.post.__wrapped__(handler)

    assert calls == [("update-check", (None, None, None, None, None, None, None, None, None, None))]
    assert handler.response["data"]["latest_version"] == "0.33.0"


@pytest.mark.asyncio
async def test_password_change_updates_only_logged_in_user_after_verification(monkeypatch):
    handler = _FakeHandler(
        b'{"current_password":"OldPass12","new_password":"NewPass34","confirm_password":"NewPass34"}',
        username="user01",
    )
    calls = []
    monkeypatch.setattr(password, "_hpc_validate_password", lambda value: None)
    monkeypatch.setattr(
        password,
        "_hpc_verify_linux_password",
        lambda username, value, service: calls.append(("verify", username, value, service)),
    )
    monkeypatch.setattr(
        password,
        "_hpc_set_linux_password",
        lambda username, value: calls.append(("set", username, value)),
    )
    monkeypatch.setattr(
        password,
        "_hpc_log_password_success",
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
        password,
        "_hpc_verify_linux_password",
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
        llm_api,
        "_hpc_litellm_regenerate_own_key",
        lambda username: usernames.append(username) or ("sk-new-key", None),
    )
    monkeypatch.setattr(llm_api, "HPC_LITELLM_PUBLIC_BASE_URL", "https://llm.example.test/v1")

    await llm_api.HpcLlmApiApiHandler.post.__wrapped__(handler)

    assert usernames == ["user01"]
    assert handler.response == {
        "ok": True,
        "api_key": "sk-new-key",
        "api_base_url": "https://llm.example.test/v1",
    }


@pytest.mark.asyncio
async def test_llm_api_rejects_unknown_action_without_key_operation(monkeypatch):
    handler = _FakeHandler(b'{"action":"delete"}', username="user01")
    monkeypatch.setattr(
        llm_api,
        "_hpc_litellm_regenerate_own_key",
        lambda username: pytest.fail("キー操作は禁止"),
    )

    await llm_api.HpcLlmApiApiHandler.post.__wrapped__(handler)

    assert handler.status == 400
    assert handler.response == {"error": "不明な action です"}
