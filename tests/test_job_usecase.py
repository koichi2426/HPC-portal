"""アプリ停止時はHub側の失敗にかかわらず残存Slurmジョブも回収する。"""

from types import SimpleNamespace

from hpc_portal.application.usecase.job_management_usecase import (
    StopUserOpenWebuiJobsUseCase,
)
from hpc_portal.infrastructure.jupyterhub.user_jobs import HubUserJobs


def test_hub_adapter_lists_only_running_or_pending_target_application():
    def spawner(app, active=False, pending=None):
        return SimpleNamespace(
            user_options={"app_choice": app}, active=active, pending=pending
        )

    user = SimpleNamespace(
        spawners={
            "webui": spawner("open-webui", active=True),
            "starting": spawner("open-webui", pending="spawn"),
            "stopped": spawner("open-webui"),
            "notebook": spawner("ubuntu-cli", active=True),
        }
    )
    gateway = HubUserJobs(lambda: SimpleNamespace(users={"alice": user}))
    assert gateway.active_servers("alice", "open-webui") == ["webui", "starting"]
    assert gateway.active_servers("bob", "open-webui") == []


async def test_hub_stop_failure_still_cancels_remaining_slurm_jobs():
    commands = []
    audit = []

    async def stop_server(username, name):
        raise RuntimeError("Hub stop failed")

    def run(command):
        commands.append(command)
        return SimpleNamespace(
            returncode=0, stdout="21\n22\n" if command[0] == "squeue" else "", stderr=""
        )

    gateway = SimpleNamespace(
        safe_litellm_error=lambda error: str(error),
        log_litellm_action=lambda *args: audit.append(args),
    )
    usecase = StopUserOpenWebuiJobsUseCase(
        user_jobs=SimpleNamespace(
            active_servers=lambda username, app: ["webui"], stop_server=stop_server
        ),
        commands=SimpleNamespace(run=run),
        gateway=gateway,
    )
    error = await usecase.execute("alice")
    assert error == "Hub stop failed"
    assert commands[0] == [
        "squeue",
        "-h",
        "-u",
        "alice",
        "-n",
        "jhub-openwebui",
        "-o",
        "%A",
    ]
    assert commands[-1] == ["scancel", "21", "22"]
    assert audit[-1][:3] == ("openwebui_stop", "alice", "failed")
