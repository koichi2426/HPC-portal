"""Open WebUI専用Keyのパス安全性、所有権、原子的保存を検証する。"""

import stat

import pytest

from hpc_portal.infrastructure.openwebui.api_key_store import OpenWebuiKeyStore


def test_key_store_preserves_configured_reserved_users(tmp_path):
    store = OpenWebuiKeyStore(str(tmp_path), protected_users={"operator"})

    with pytest.raises(ValueError):
        store.path("operator")
    assert store.path("user01") == str(tmp_path / "user01.key")


@pytest.mark.parametrize("username", ["../root", "a/b", "user;id", "$(id)", "ab"])
def test_openwebui_key_path_rejects_path_traversal_and_invalid_usernames(username):
    with pytest.raises(ValueError):
        openwebui.key_store.path(username)


def test_openwebui_key_is_written_atomically_with_owner_only_permissions(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(openwebui.key_store, "directory", str(tmp_path))

    assert openwebui.key_store.write("user01", "sk-test-key") is None

    path = tmp_path / "user01.key"
    assert path.read_text() == "sk-test-key\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(tmp_path.glob("*.tmp-*"))


def test_openwebui_key_empty_value_is_not_written(monkeypatch, tmp_path):
    monkeypatch.setattr(openwebui.key_store, "directory", str(tmp_path))

    error = openwebui.key_store.write("user01", "")

    assert error
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("data", "state", "message"),
    [
        ({"info": {"user_id": "user01", "blocked": False}}, "valid", None),
        ({"info": {"user_id": "user01", "blocked": "true"}}, "blocked", None),
        ({"info": {"user_id": "other", "blocked": False}}, "mismatch", "一致しません"),
        ({"info": {"blocked": False}}, "mismatch", "設定されていません"),
    ],
)
def test_openwebui_key_info_checks_owner_and_block_state(
    monkeypatch, data, state, message
):
    monkeypatch.setattr(
        openwebui.client,
        "request",
        lambda path, method="POST": data,
    )

    _info, actual_state, error = openwebui.openwebui_key_info("user01", "sk-key")

    assert actual_state == state
    if message:
        assert message in error
    else:
        assert error is None


def test_openwebui_key_info_treats_not_found_as_missing(monkeypatch):
    monkeypatch.setattr(
        openwebui.client,
        "request",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("LiteLLM API HTTP 404: token_not_found")
        ),
    )

    assert openwebui.openwebui_key_info("user01", "sk-key") == (
        None,
        "missing",
        None,
    )


def test_get_openwebui_key_reuses_valid_saved_key(monkeypatch):
    monkeypatch.setattr(openwebui.client, "enabled", lambda: True)
    monkeypatch.setattr(openwebui, "ensure_user", lambda username: None)
    monkeypatch.setattr(
        openwebui, "user_admin_disabled", lambda username: (False, None)
    )
    monkeypatch.setattr(openwebui.key_store, "read", lambda username: "sk-existing")
    monkeypatch.setattr(
        openwebui,
        "openwebui_key_info",
        lambda username, key: ({"user_id": username}, "valid", None),
    )
    monkeypatch.setattr(
        openwebui,
        "generate_openwebui_key",
        lambda username: pytest.fail("再発行は禁止"),
    )

    assert openwebui.get_openwebui_key("user01") == (
        "sk-existing",
        None,
    )


def test_get_openwebui_key_blocks_new_key_when_file_save_fails(monkeypatch):
    calls = []
    monkeypatch.setattr(openwebui.client, "enabled", lambda: True)
    monkeypatch.setattr(openwebui, "ensure_user", lambda username: None)
    monkeypatch.setattr(
        openwebui, "user_admin_disabled", lambda username: (False, None)
    )
    monkeypatch.setattr(openwebui.key_store, "read", lambda username: "")
    monkeypatch.setattr(
        openwebui, "generate_openwebui_key", lambda username: ("sk-new", None)
    )
    monkeypatch.setattr(
        openwebui.key_store, "write", lambda username, key: "disk failed"
    )
    monkeypatch.setattr(
        openwebui.client,
        "request",
        lambda path, payload: calls.append((path, payload)) or {},
    )

    generated, error = openwebui.get_openwebui_key("user01")

    assert generated is None
    assert error == "disk failed"
    assert calls == [("/key/block", {"key": "sk-new"})]


@pytest.fixture(autouse=True)
def usecase_components(portal_dependencies):
    global openwebui, portal, ollama
    portal = portal_dependencies
    openwebui = portal_dependencies.llm
    ollama = portal_dependencies.ollama.client
