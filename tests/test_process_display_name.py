"""起動対象名の判別と、別ユーザーの起動引数を読まないことを確認する。"""

from types import SimpleNamespace
from unittest.mock import Mock

import psutil
import pytest

from hpc_portal.infrastructure.linux import process_display_name as process_names


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (
            ["python3.12", "-u", "/home/alice/server.py", "--token", "secret"],
            "server.py（Python）",
        ),
        (["python", "-X", "dev", "-m", "package.api"], "package.api（Python）"),
        (
            ["node", "--require", "setup.js", "/home/alice/server.js"],
            "server.js（Node.js）",
        ),
        (["java", "-Dtoken=secret", "-jar", "/home/alice/app.jar"], "app.jar（Java）"),
        (["api-server", "--token", "secret"], "api-server"),
        (["python", "-c", "secret.py"], "python"),
        (["node", "--unknown", "secret.js"], "node"),
        (["java", "Main", "-jar", "secret.jar"], "java"),
    ],
)
def test_command_name_extracts_only_known_entrypoints(arguments, expected):
    """一般的な起動対象だけを表示し、コードや不明なオプションの値は出さない。

    Args:
        arguments: 判別対象の起動引数。
        expected: ファイル名またはフォールバックするプロセス名。
    """
    assert process_names.command_display_name(arguments[0], arguments) == expected


@pytest.mark.parametrize(("uid", "started_at"), [(1002, 100.0), (1001, 200.0)])
def test_changed_process_does_not_read_arguments(monkeypatch, uid, started_at):
    """所有者や開始時刻が変わっていれば起動引数を読み取らない。

    Args:
        monkeypatch: プロセス情報を差し替えるpytestの機能。
        uid: 現在のプロセスの所有者。
        started_at: 現在のプロセスの開始時刻。
    """
    arguments = Mock(return_value=["python", "secret.py"])
    process = SimpleNamespace(
        uids=lambda: SimpleNamespace(real=uid, effective=uid),
        create_time=lambda: started_at,
        cmdline=arguments,
    )
    monkeypatch.setattr(process_names.psutil, "Process", lambda pid: process)
    snapshot = {"pid": 42, "uid": 1001, "started_at": 100.0, "display_name": "python"}

    assert process_names.resolve_process_display_name(snapshot) == "python"
    arguments.assert_not_called()


def test_unreadable_arguments_fall_back_to_process_name(monkeypatch):
    """起動引数を読めなくても起動済みアプリの候補を失わない。

    Args:
        monkeypatch: プロセス情報を差し替えるpytestの機能。
    """
    process = SimpleNamespace(
        uids=lambda: SimpleNamespace(real=1001, effective=1001),
        create_time=lambda: 100.0,
        cmdline=Mock(side_effect=psutil.AccessDenied(42)),
    )
    monkeypatch.setattr(process_names.psutil, "Process", lambda pid: process)
    snapshot = {"pid": 42, "uid": 1001, "started_at": 100.0, "display_name": "python"}

    assert process_names.resolve_process_display_name(snapshot) == "python"
