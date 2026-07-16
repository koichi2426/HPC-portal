"""LiteLLMユーザーmetadataによるAPI停止状態管理を検証する。"""

import pytest

from hpc_portal.litellm import users


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"admin_disabled": True}, {"admin_disabled": True}),
        ('{"admin_disabled":false}', {"admin_disabled": False}),
        ("[]", {}),
        ("invalid", {}),
        (None, {}),
    ],
)
def test_litellm_metadata_normalizes_supported_values(value, expected):
    assert users._hpc_litellm_metadata(value) == expected


def test_ensure_user_treats_duplicate_as_success(monkeypatch):
    monkeypatch.setattr(users, "_hpc_litellm_enabled", lambda: True)
    monkeypatch.setattr(
        users,
        "_hpc_litellm_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("user already exists")),
    )

    assert users._hpc_litellm_ensure_user("user01") is None


def test_ensure_user_sends_expected_identity_metadata(monkeypatch):
    calls = []
    monkeypatch.setattr(users, "_hpc_litellm_enabled", lambda: True)
    monkeypatch.setattr(users, "_hpc_litellm_request", lambda path, payload: calls.append((path, payload)) or {})

    assert users._hpc_litellm_ensure_user("user01") is None
    assert calls[0][0] == "/user/new"
    assert calls[0][1]["user_id"] == "user01"
    assert calls[0][1]["metadata"]["admin_disabled"] is False


def test_user_metadata_url_encodes_username(monkeypatch):
    paths = []
    monkeypatch.setattr(users, "_hpc_litellm_enabled", lambda: True)
    monkeypatch.setattr(
        users,
        "_hpc_litellm_request",
        lambda path, method="POST": paths.append((path, method))
        or {"user_info": {"metadata": '{"admin_disabled":true}'}},
    )

    metadata, error = users._hpc_litellm_user_metadata("name/with?query")

    assert error is None
    assert metadata == {"admin_disabled": True}
    assert paths == [("/user/info?user_id=name%2Fwith%3Fquery", "GET")]


def test_admin_disabled_requires_literal_boolean_true(monkeypatch):
    monkeypatch.setattr(users, "_hpc_litellm_user_metadata", lambda username: ({"admin_disabled": "true"}, None))
    assert users._hpc_litellm_user_admin_disabled("user01") == (False, None)

    monkeypatch.setattr(users, "_hpc_litellm_user_metadata", lambda username: ({"admin_disabled": True}, None))
    assert users._hpc_litellm_user_admin_disabled("user01") == (True, None)

