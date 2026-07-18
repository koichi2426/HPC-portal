"""JupyterHub再起動後のジョブ用CHPルート復元を検証する。"""

import pytest

from hpc_portal.proxy import _hpc_running_job_route_target


class _FakeSpawner:
    """ルート復元判定に必要なSpawner状態だけを提供する。"""

    def __init__(self, poll_result=None, job_id="67", port=20067):
        """JOB ID、ポート、poll結果を設定する。

        Args:
            poll_result: ``poll`` が返す終了状態。Noneは実行中。
            job_id: 保存済みSlurm JOB ID。
            port: 保存済みアプリ待受ポート。
        """
        self.job_id = job_id
        self.port = port
        self.server = None
        self._poll_result = poll_result

    async def poll(self):
        """設定されたジョブ終了状態を返す。"""
        return self._poll_result

    def state_isrunning(self):
        """poll結果が実行中を表すか返す。"""
        return self._poll_result is None

    def get_state(self):
        """保存済みJOB IDをSpawner state形式で返す。"""
        return {"job_id": self.job_id}


@pytest.mark.asyncio
async def test_running_slurm_job_restores_target_without_ready_server():
    """ready前でも実行中ジョブと保存済みポートから転送先を復元する。"""
    spawner = _FakeSpawner()

    target = await _hpc_running_job_route_target(spawner)

    assert target == "http://127.0.0.1:20067"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "spawner",
    [
        _FakeSpawner(poll_result=1),
        _FakeSpawner(job_id=""),
        _FakeSpawner(job_id="invalid", port=0),
    ],
)
async def test_stopped_or_incomplete_job_does_not_restore_target(spawner):
    """停止済みまたは接続情報不足のジョブをルートへ戻さない。"""
    assert await _hpc_running_job_route_target(spawner) == ""
