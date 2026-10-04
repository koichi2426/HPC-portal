"""起動失敗時の説明と、PENDING理由の取り出しを検証する。"""

from types import SimpleNamespace

from hpc_portal.infrastructure.jupyterhub import slurm_spawner as spawner


class _FakeSpawner:
    """hpc_failure_message だけを取り出して検証するための最小構成。"""

    hpc_failure_message = spawner.HPCSlurmSpawner.hpc_failure_message

    def __init__(self, failure, pending_reason=""):
        self._failed = failure
        self._hpc_pending_reason = pending_reason


def test_failure_message_masks_secrets():
    failure = SimpleNamespace(jupyterhub_message="token sk-abcdefgh1234 leaked")

    message = _FakeSpawner(failure).hpc_failure_message

    assert "sk-***" in message
    assert "sk-abcdefgh1234" not in message
