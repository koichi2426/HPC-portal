"""Linuxユーザー管理の入力境界と安全なコマンド構築を検証する。"""

from types import SimpleNamespace

import pytest

from hpc_portal import users


@pytest.mark.parametrize("username", ["abc", "user01", "a_b-c", "u" + "1" * 31])
def test_valid_username_is_accepted(username):
    assert users._hpc_validate_username(username) is None


@pytest.mark.parametrize(
    "username",
    ["", "ab", "a" * 33, "Admin", "root", "../user", "a;id", "ユーザー", "a b"],
)
def test_invalid_or_protected_username_is_rejected(username):
    assert users._hpc_validate_username(username) is not None


@pytest.mark.parametrize("password", ["Abcdef12", "日本語Pass1", "A" * 1000 + "a1"])
def test_valid_password_is_accepted(password):
    assert users._hpc_validate_password(password) is None


@pytest.mark.parametrize("password", ["", "Abcd123", "Abcd:123", "Abcd\n123", "Abcd\r123"])
def test_invalid_password_is_rejected(password):
    assert users._hpc_validate_password(password) is not None


@pytest.mark.parametrize("display_name", ["", "研究 太郎", "名前🚀", "a" * 80])
def test_valid_display_name_is_accepted(display_name):
    assert users._hpc_validate_display_name(display_name) is None


@pytest.mark.parametrize("display_name", ["a" * 81, "姓,名", "name:admin", "line\nbreak"])
def test_invalid_display_name_is_rejected(display_name):
    assert users._hpc_validate_display_name(display_name) is not None


def test_generated_password_has_required_length_and_character_classes():
    passwords = {users._hpc_generate_password() for _ in range(20)}

    assert len(passwords) > 1
    assert all(len(password) == 12 for password in passwords)
    assert all(any(char.isupper() for char in password) for password in passwords)
    assert all(any(char.islower() for char in password) for password in passwords)
    assert all(any(char.isdigit() for char in password) for password in passwords)


def test_home_storage_usage_passes_path_as_single_argument(monkeypatch, tmp_path):
    home = tmp_path / "home with ; metacharacters"
    home.mkdir()
    seen = {}

    def fake_run(command, **kwargs):
        """実行予定の引数を記録してduの成功結果を返す。"""
        seen["command"] = command
        seen["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="1024\tpath\n", stderr="")

    monkeypatch.setattr(users.subprocess, "run", fake_run)

    assert users._hpc_home_storage_usage(str(home)) == (1024, None)
    assert seen["command"] == ["du", "-s", "-x", "-B1", "--", str(home)]
    assert seen["kwargs"]["timeout"] == 30


@pytest.mark.parametrize(
    "exception",
    [users.subprocess.TimeoutExpired(cmd="du", timeout=30), OSError("failed")],
)
def test_home_storage_usage_handles_command_failure(monkeypatch, tmp_path, exception):
    monkeypatch.setattr(users.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(exception))

    size, error = users._hpc_home_storage_usage(str(tmp_path))

    assert size is None
    assert error


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
    monkeypatch.setattr(users, "_hpc_run_cmd", fake_run)

    error = users._hpc_create_linux_user("user01", "Abcd1234", True, "研究 太郎")

    assert error == "password failed"
    assert commands == [
        (["useradd", "-m", "-K", "HOME_MODE=0700", "-s", "/bin/bash", "-c", "研究 太郎", "-G", "sudo", "user01"], None),
        (["chpasswd"], "user01:Abcd1234"),
        (["userdel", "-r", "user01"], None),
    ]


def test_delete_user_rejects_protected_and_self_without_commands(monkeypatch):
    calls = []
    monkeypatch.setattr(users, "_hpc_run_cmd", lambda command, **kwargs: calls.append(command))

    assert users._hpc_delete_linux_user("admin", "operator") is not None
    assert users._hpc_delete_linux_user("operator", "operator") is not None
    assert calls == []


def test_delete_user_stops_jobs_and_processes_before_deletion(monkeypatch):
    commands = []
    monkeypatch.setattr(users.pwd, "getpwnam", lambda username: SimpleNamespace())
    monkeypatch.setattr(
        users,
        "_hpc_run_cmd",
        lambda command, **kwargs: commands.append(command)
        or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    assert users._hpc_delete_linux_user("user01", "admin") is None
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

    monkeypatch.setattr(users, "_hpc_run_cmd", fake_run)

    assert users._hpc_set_linux_password("user01", "Abcd1234") is None
    assert observed["command"] == ["chpasswd"]
    assert observed["input_text"] == "user01:Abcd1234"

