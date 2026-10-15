"""LiteLLM外部API Keyの所有権、再発行、停止時ロールバックを検証する。"""

from types import SimpleNamespace

import pytest


def test_generate_key_uses_user_scoped_alias_and_metadata(monkeypatch):
    calls = []
    monkeypatch.setattr(keys.client, "enabled", lambda: True)
    monkeypatch.setattr(keys.gateway, "ensure_user", lambda username: None)
    monkeypatch.setattr(
        keys.client,
        "request",
        lambda path, payload: (
            calls.append((path, payload)) or {"token": "sk-user-token"}
        ),
    )

    generated, error = keys.generate_key.execute("user01")

    assert (generated, error) == ("sk-user-token", None)
    assert calls[0][0] == "/key/generate"
    assert calls[0][1]["user_id"] == "user01"
    assert calls[0][1]["key_alias"] == "user01"
    assert calls[0][1]["metadata"]["linux_username"] == "user01"


def test_list_user_keys_filters_other_users_and_encodes_query(monkeypatch):
    paths = []
    response = {
        "keys": [
            {"key": "owned", "user_id": "name/with?query"},
            {"key": "other", "user_id": "other"},
        ]
    }
    monkeypatch.setattr(keys.client, "enabled", lambda: True)
    monkeypatch.setattr(
        keys.client,
        "request",
        lambda path, method="POST": paths.append((path, method)) or response,
    )

    records, error = keys.gateway.list_user_keys("name/with?query")

    assert error is None
    assert records == [{"key": "owned", "user_id": "name/with?query"}]
    assert paths[0] == ("/key/list?user_id=name%2Fwith%3Fquery", "GET")


def test_delete_external_keys_never_deletes_openwebui_key(monkeypatch):
    calls = []
    listings = iter(
        [
            (
                [
                    {"key": "external-id", "key_alias": "user01"},
                    {
                        "key": "openwebui-id",
                        "key_alias": "openwebui-user01",
                        "metadata": {"source": "hpc-portal-openwebui"},
                    },
                ],
                None,
            ),
            ([{"key": "openwebui-id", "key_alias": "openwebui-user01"}], None),
        ]
    )
    monkeypatch.setattr(keys.gateway, "list_user_keys", lambda username: next(listings))
    monkeypatch.setattr(
        keys.client,
        "request",
        lambda path, payload: calls.append((path, payload)) or {},
    )

    assert keys.delete_portal_external_keys.execute("user01") is None
    assert calls == [
        ("/key/block", {"key": "external-id"}),
        ("/key/delete", {"key_aliases": ["user01"]}),
    ]


def test_regenerate_key_refuses_admin_disabled_user_before_deletion(monkeypatch):
    monkeypatch.setattr(keys.client, "enabled", lambda: True)
    monkeypatch.setattr(
        keys.regenerate_own_key.accounts, "getpwnam", lambda username: SimpleNamespace()
    )
    monkeypatch.setattr(
        keys.gateway, "user_admin_disabled", lambda username: (True, None)
    )
    monkeypatch.setattr(
        keys.delete_portal_external_keys,
        "execute",
        lambda username: pytest.fail("削除は禁止"),
    )

    generated, error = keys.regenerate_own_key.execute("user01")

    assert generated is None
    assert "管理者により無効化" in error


def test_enable_api_rolls_back_all_keys_when_openwebui_unblock_fails(monkeypatch):
    calls = []
    monkeypatch.setattr(keys.client, "enabled", lambda: True)
    monkeypatch.setattr(
        keys.regenerate_own_key.accounts, "getpwnam", lambda username: SimpleNamespace()
    )
    monkeypatch.setattr(keys.gateway, "ensure_user", lambda username: None)

    def set_external(username, blocked, **kwargs):
        """外部Keyのblock状態変更を記録する。"""
        calls.append(("external", blocked, kwargs))
        return None

    def set_openwebui(username, blocked):
        """最初のunblockだけ失敗し、ロールバックblockは成功させる。"""
        calls.append(("openwebui", blocked))
        return "unblock failed" if blocked is False else None

    monkeypatch.setattr(keys.set_user_keys_blocked, "execute", set_external)
    monkeypatch.setattr(keys.set_openwebui_key_blocked, "execute", set_openwebui)
    monkeypatch.setattr(
        keys.gateway,
        "set_user_admin_disabled",
        lambda username, disabled: calls.append(("user", disabled)),
    )

    generated, error = keys.admin_set_api_access.execute("user01", True)

    assert generated is None
    assert error == "unblock failed"
    assert (
        "external",
        True,
        {"mark_admin_disabled": True, "include_openwebui": False},
    ) in calls
    assert ("openwebui", True) in calls
    assert ("user", True) in calls


def test_disable_api_marks_user_disabled_before_blocking_keys(monkeypatch):
    calls = []
    monkeypatch.setattr(keys.client, "enabled", lambda: True)
    monkeypatch.setattr(
        keys.regenerate_own_key.accounts, "getpwnam", lambda username: SimpleNamespace()
    )
    monkeypatch.setattr(
        keys.gateway,
        "set_user_admin_disabled",
        lambda username, disabled: calls.append(("user", disabled)),
    )
    monkeypatch.setattr(
        keys.set_user_keys_blocked,
        "execute",
        lambda username, blocked, **kwargs: calls.append(("external", blocked)),
    )
    monkeypatch.setattr(
        keys.set_openwebui_key_blocked,
        "execute",
        lambda username, blocked: calls.append(("openwebui", blocked)),
    )

    assert keys.admin_set_api_access.execute("user01", False) == (None, None)
    assert calls == [("user", True), ("external", True), ("openwebui", True)]


@pytest.fixture(autouse=True)
def usecase_components(portal_dependencies):
    global keys, portal, ollama
    portal = portal_dependencies
    keys = portal_dependencies.llm
    ollama = portal_dependencies.ollama.backend
