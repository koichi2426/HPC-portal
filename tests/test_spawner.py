"""起動失敗時の説明と、PENDING理由の取り出しを検証する。"""

from types import SimpleNamespace

import pytest

from hpc_portal import spawner


@pytest.mark.parametrize(
    ("stdout", "returncode", "expected"),
    [
        ("(Resources)\n", 0, "Resources"),
        ("  (Priority)  \n", 0, "Priority"),
        ("gx10-ac12\n", 0, ""),  # RUNNING はノード名を返すため理由ではない
        ("", 0, ""),
        ("(Resources)\n", 1, ""),
    ],
)
def test_slurm_pending_reason(monkeypatch, stdout, returncode, expected):
    monkeypatch.setattr(
        spawner.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=returncode, stdout=stdout),
    )

    assert spawner._hpc_slurm_pending_reason("42") == expected


def test_slurm_pending_reason_without_job_id():
    assert spawner._hpc_slurm_pending_reason("") == ""


def test_slurm_pending_reason_survives_command_failure(monkeypatch):
    def raise_oserror(*args, **kwargs):
        raise OSError("squeue not found")

    monkeypatch.setattr(spawner.subprocess, "run", raise_oserror)

    assert spawner._hpc_slurm_pending_reason("42") == ""


@pytest.mark.parametrize(
    ("reason", "fragment"),
    [
        ("Resources", "GPUまたはメモリの空き待ち"),
        ("Priority", "他のジョブが優先"),
        ("SomethingNew", "空きリソース待ち"),
        ("", ""),
    ],
)
def test_pending_reason_message(reason, fragment):
    message = spawner._hpc_pending_reason_message(reason)

    if fragment:
        assert fragment in message
        assert reason in message
    else:
        assert message == ""


class _FakeSpawner:
    """hpc_failure_message だけを取り出して検証するための最小構成。"""

    hpc_failure_message = spawner.HPCSlurmSpawner.hpc_failure_message

    def __init__(self, failure, pending_reason=""):
        self._failed = failure
        self._hpc_pending_reason = pending_reason


def test_failure_message_appends_pending_reason():
    """start_timeout到達時に、待たされた理由を添えることを確認する。"""
    failure = SimpleNamespace(jupyterhub_message="Server did not start in 300 seconds")

    message = _FakeSpawner(failure, "Resources").hpc_failure_message

    assert "起動待ちのまま時間切れ" in message
    assert "GPUまたはメモリの空き待ち" in message
    assert "Resources" in message


def test_failure_message_keeps_pending_reason_when_body_is_long():
    """長い例外文に押し出されて理由が切り捨てられないことを確認する。"""
    failure = SimpleNamespace(jupyterhub_message="x" * 500)

    message = _FakeSpawner(failure, "Resources").hpc_failure_message

    assert message.count("x") == 300
    assert "GPUまたはメモリの空き待ち" in message


def test_failure_message_without_pending_reason():
    failure = SimpleNamespace(jupyterhub_message="boom")

    assert _FakeSpawner(failure).hpc_failure_message == "boom"


def test_failure_message_is_empty_without_failure():
    assert _FakeSpawner(None).hpc_failure_message == ""


def test_failure_message_masks_secrets():
    failure = SimpleNamespace(jupyterhub_message="token sk-abcdefgh1234 leaked")

    message = _FakeSpawner(failure).hpc_failure_message

    assert "sk-***" in message
    assert "sk-abcdefgh1234" not in message


def test_pending_reason_is_not_refetched_every_iteration(monkeypatch):
    """進捗ループは0.75秒間隔で回るため、待機理由の取得を間引く。

    毎周叩くと300秒のPENDINGで約400回になる。
    """
    calls = []
    monkeypatch.setattr(
        spawner.subprocess,
        "run",
        lambda *args, **kwargs: calls.append(1)
        or SimpleNamespace(returncode=0, stdout="(Resources)\n"),
    )

    class _Sp:
        _hpc_pending_reason = ""
        _hpc_pending_reason_checked_at = 0.0

    sp = _Sp()
    clock = [1000.0]
    monkeypatch.setattr(spawner.time, "monotonic", lambda: clock[0])

    def tick():
        """progress ループ1周分の判定を再現する。"""
        now = spawner.time.monotonic()
        if now >= sp._hpc_pending_reason_checked_at:
            sp._hpc_pending_reason_checked_at = now + spawner._HPC_PENDING_REASON_INTERVAL
            reason = spawner._hpc_slurm_pending_reason("42")
            if reason:
                sp._hpc_pending_reason = reason

    # 10秒ぶん（約13周）回しても取得は1回
    for _ in range(13):
        tick()
        clock[0] += 0.75
    assert len(calls) == 1

    # 間隔を超えれば取り直す
    clock[0] += spawner._HPC_PENDING_REASON_INTERVAL
    tick()
    assert len(calls) == 2
    assert sp._hpc_pending_reason == "Resources"
