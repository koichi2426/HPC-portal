"""LiteLLM外部API Keyの所有権、再発行、停止時ロールバックを検証する。"""

from types import SimpleNamespace

import pytest

from hpc_portal.litellm import keys


def test_generate_key_uses_user_scoped_alias_and_metadata(monkeypatch):
    calls = []
    monkeypatch.setattr(keys, "_hpc_litellm_enabled", lambda: True)
    monkeypatch.setattr(keys, "_hpc_litellm_ensure_user", lambda username: None)
    monkeypatch.setattr(
        keys,
        "_hpc_litellm_request",
        lambda path, payload: calls.append((path, payload)) or {"token": "sk-user-token"},
    )

    generated, error = keys._hpc_litellm_generate_key("user01")

    assert (generated, error) == ("sk-user-token", None)
    assert calls[0][0] == "/key/generate"
    assert calls[0][1]["user_id"] == "user01"
    assert calls[0][1]["key_alias"] == "user01"
    assert calls[0][1]["metadata"]["linux_username"] == "user01"


def test_key_record_belongs_only_to_matching_user():
    record = {
        "user_id": "other",
        "key_alias": "another",
        "metadata": '{"linux_username":"user01"}',
    }

    assert keys._hpc_litellm_key_belongs_to_user(record, "user01") is True
    assert keys._hpc_litellm_key_belongs_to_user(record, "unknown") is False


def test_list_user_keys_filters_other_users_and_encodes_query(monkeypatch):
    paths = []
    response = {
        "keys": [
            {"key": "owned", "user_id": "name/with?query"},
            {"key": "other", "user_id": "other"},
        ]
    }
    monkeypatch.setattr(keys, "_hpc_litellm_enabled", lambda: True)
    monkeypatch.setattr(
        keys,
        "_hpc_litellm_request",
        lambda path, method="POST": paths.append((path, method)) or response,
    )

    records, error = keys._hpc_litellm_list_user_keys("name/with?query")

    assert error is None
    assert records == [{"key": "owned", "user_id": "name/with?query"}]
    assert paths[0] == ("/key/list?user_id=name%2Fwith%3Fquery", "GET")


@pytest.mark.parametrize(
    ("disabled_result", "records_result", "expected"),
    [
        ((False, "LiteLLM API HTTP 404: not found"), ([], None), ("unissued", None)),
        ((True, None), ([], None), ("disabled", None)),
        ((False, None), ([{"key_alias": "user01"}], None), ("enabled", None)),
        ((False, None), ([{"key_alias": "openwebui-user01"}], None), ("unissued", None)),
    ],
)
def test_external_api_state_mapping(monkeypatch, disabled_result, records_result, expected):
    monkeypatch.setattr(keys, "_hpc_litellm_user_admin_disabled", lambda username: disabled_result)
    monkeypatch.setattr(keys, "_hpc_litellm_list_user_keys", lambda username: records_result)

    assert keys._hpc_litellm_user_external_api_state("user01") == expected


def test_delete_external_keys_never_deletes_openwebui_key(monkeypatch):
    calls = []
    listings = iter(
        [
            (
                [
                    {"key": "external-id", "key_alias": "user01"},
                    {"key": "openwebui-id", "key_alias": "openwebui-user01", "metadata": {"source": "hpc-portal-openwebui"}},
                ],
                None,
            ),
            ([{"key": "openwebui-id", "key_alias": "openwebui-user01"}], None),
        ]
    )
    monkeypatch.setattr(keys, "_hpc_litellm_list_user_keys", lambda username: next(listings))
    monkeypatch.setattr(
        keys,
        "_hpc_litellm_request",
        lambda path, payload: calls.append((path, payload)) or {},
    )

    assert keys._hpc_litellm_delete_portal_external_keys("user01") is None
    assert calls == [
        ("/key/block", {"key": "external-id"}),
        ("/key/delete", {"key_aliases": ["user01"]}),
    ]


def test_regenerate_key_refuses_admin_disabled_user_before_deletion(monkeypatch):
    monkeypatch.setattr(keys, "_hpc_litellm_enabled", lambda: True)
    monkeypatch.setattr(keys.pwd, "getpwnam", lambda username: SimpleNamespace())
    monkeypatch.setattr(keys, "_hpc_litellm_user_admin_disabled", lambda username: (True, None))
    monkeypatch.setattr(
        keys,
        "_hpc_litellm_delete_portal_external_keys",
        lambda username: pytest.fail("削除は禁止"),
    )

    generated, error = keys._hpc_litellm_regenerate_own_key("user01")

    assert generated is None
    assert "管理者により無効化" in error


def test_enable_api_rolls_back_all_keys_when_openwebui_unblock_fails(monkeypatch):
    calls = []
    monkeypatch.setattr(keys, "_hpc_litellm_enabled", lambda: True)
    monkeypatch.setattr(keys.pwd, "getpwnam", lambda username: SimpleNamespace())
    monkeypatch.setattr(keys, "_hpc_litellm_ensure_user", lambda username: None)

    def set_external(username, blocked, **kwargs):
        """外部Keyのblock状態変更を記録する。"""
        calls.append(("external", blocked, kwargs))
        return None

    def set_openwebui(username, blocked):
        """最初のunblockだけ失敗し、ロールバックblockは成功させる。"""
        calls.append(("openwebui", blocked))
        return "unblock failed" if blocked is False else None

    monkeypatch.setattr(keys, "_hpc_litellm_set_user_keys_blocked", set_external)
    monkeypatch.setattr(keys, "_hpc_litellm_set_openwebui_key_blocked", set_openwebui)
    monkeypatch.setattr(
        keys,
        "_hpc_litellm_set_user_admin_disabled",
        lambda username, disabled: calls.append(("user", disabled)),
    )

    generated, error = keys._hpc_litellm_admin_set_api_access("user01", True)

    assert generated is None
    assert error == "unblock failed"
    assert ("external", True, {"mark_admin_disabled": True, "include_openwebui": False}) in calls
    assert ("openwebui", True) in calls
    assert ("user", True) in calls


def test_disable_api_marks_user_disabled_before_blocking_keys(monkeypatch):
    calls = []
    monkeypatch.setattr(keys, "_hpc_litellm_enabled", lambda: True)
    monkeypatch.setattr(keys.pwd, "getpwnam", lambda username: SimpleNamespace())
    monkeypatch.setattr(
        keys,
        "_hpc_litellm_set_user_admin_disabled",
        lambda username, disabled: calls.append(("user", disabled)),
    )
    monkeypatch.setattr(
        keys,
        "_hpc_litellm_set_user_keys_blocked",
        lambda username, blocked, **kwargs: calls.append(("external", blocked)),
    )
    monkeypatch.setattr(
        keys,
        "_hpc_litellm_set_openwebui_key_blocked",
        lambda username, blocked: calls.append(("openwebui", blocked)),
    )

    assert keys._hpc_litellm_admin_set_api_access("user01", False) == (None, None)
    assert calls == [("user", True), ("external", True), ("openwebui", True)]

