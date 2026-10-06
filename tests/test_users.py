"""Linuxユーザー管理の入力境界と安全なコマンド構築を検証する。"""

from types import SimpleNamespace

import pytest

from hpc_portal.application.usecase.account_management_usecase import (
    ListAccountsUseCase,
)
from hpc_portal.infrastructure.linux import user_account_gateway as users


@pytest.mark.parametrize(
    "record, expected_enabled, expected_state",
    [
        ({"enabled": True, "state": "ready"}, True, "ready"),
        ({"enabled": False, "state": "revoking"}, False, "revoking"),
        (
            {"enabled": True, "state": "rotating_cloudflare"},
            True,
            "rotating_cloudflare",
        ),
        (None, True, "issuing"),
    ],
)
async def test_account_list_separates_api_permission_from_credential_state(
    record, expected_enabled, expected_state
):
    """発行・更新・失効の途中でも、利用許可を発行状態と分けて一覧へ返す。"""
    usecase = ListAccountsUseCase(
        accounts=SimpleNamespace(
            linux_users_snapshot=lambda: [{"username": "alice", "home": "/home/alice"}],
            home_storage_usage=lambda home: (0, None),
        ),
        llm_client=SimpleNamespace(enabled=lambda: False),
        get_llm_access_state=None,
        external_api_factory=lambda: SimpleNamespace(
            store=SimpleNamespace(get=lambda category, username: record)
        ),
    )

    row = (await usecase.execute())[0]

    assert row["external_api_enabled"] is expected_enabled
    assert row["external_api_state"] == expected_state


def test_create_user_uses_argv_and_rolls_back_when_password_setting_fails(monkeypatch):
    commands = []

    def fake_getpwnam(username):
        """作成前はユーザーが存在しない状態を返す。"""
        raise KeyError(username)

    def fake_run(command, **kwargs):
        """chpasswdだけ失敗させ、実行順と標準入力を記録する。"""
        commands.append((command, kwargs.get("input_text")))
        return SimpleNamespace(
            returncode=1 if command == ["chpasswd"] else 0,
            stdout="",
            stderr="password failed" if command == ["chpasswd"] else "",
        )

    monkeypatch.setattr(users.pwd, "getpwnam", fake_getpwnam)
    monkeypatch.setattr(users, "run_cmd", fake_run)

    error = users.create_linux_user("user01", "Abcd1234", True, "研究 太郎")

    assert error == "password failed"
    assert commands == [
        (
            [
                "useradd",
                "-m",
                "-K",
                "HOME_MODE=0700",
                "-s",
                "/bin/bash",
                "-c",
                "研究 太郎",
                "-G",
                "sudo",
                "user01",
            ],
            None,
        ),
        (["chpasswd"], "user01:Abcd1234"),
        (["userdel", "-r", "user01"], None),
    ]


def test_delete_user_rejects_protected_and_self_without_commands(monkeypatch):
    calls = []
    monkeypatch.setattr(
        users, "run_cmd", lambda command, **kwargs: calls.append(command)
    )

    assert users.delete_linux_user("admin", "operator") is not None
    assert users.delete_linux_user("operator", "operator") is not None
    assert calls == []


def test_delete_user_stops_jobs_and_processes_before_deletion(monkeypatch):
    commands = []
    monkeypatch.setattr(users.pwd, "getpwnam", lambda username: SimpleNamespace())
    monkeypatch.setattr(
        users,
        "run_cmd",
        lambda command, **kwargs: (
            commands.append(command)
            or SimpleNamespace(returncode=0, stdout="", stderr="")
        ),
    )

    assert users.delete_linux_user("user01", "admin") is None
    assert commands == [
        ["scancel", "-u", "user01"],
        ["pkill", "-u", "user01"],
        ["userdel", "-r", "user01"],
    ]


def test_set_password_uses_stdin_instead_of_command_argument(monkeypatch):
    observed = {}
    monkeypatch.setattr(users.pwd, "getpwnam", lambda username: SimpleNamespace())

    def fake_run(command, **kwargs):
        """平文パスワードがargvへ入らないことを記録する。"""
        observed.update(command=command, **kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(users, "run_cmd", fake_run)

    assert users.set_linux_password("user01", "Abcd1234") is None
    assert observed["command"] == ["chpasswd"]
    assert observed["input_text"] == "user01:Abcd1234"
