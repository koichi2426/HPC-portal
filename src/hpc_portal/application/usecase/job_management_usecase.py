"""ジョブの準備・利用権限確認・停止を行う操作手順。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from hpc_portal.application.ports.job_management_ports import CommandRunner, UserJobs
from hpc_portal.application.ports.llm_gateway_ports import LlmManagementGateway
from hpc_portal.application.ports.resource_monitoring_ports import ResourceInventory
from hpc_portal.domain.jobs.execution_policy import ExecutionPolicy
from hpc_portal.domain.jobs.execution_request import ExecutionRequest
from hpc_portal.domain.jobs.settings import JobSettings, runtime_from_hours_choice

if TYPE_CHECKING:
    from hpc_portal.application.usecase.llm_access_usecase import (
        EnsureOpenWebuiKeyUseCase,
    )


class PrepareJobUseCase:
    def __init__(
        self,
        *,
        policy: ExecutionPolicy,
        resources: ResourceInventory,
        settings: JobSettings,
    ):
        self.policy = policy
        self.resources = resources
        self.settings = settings

    def execute(self, formdata):
        """フォーム入力をSpawnerのuser_optionsへ変換する。

        Args:
            formdata: JupyterHubが渡す複数値形式のフォーム辞書。

        Returns:
            検証・正規化済みのuser_options。
        """
        app_choice = str(formdata.get("app_choice", ["ubuntu-cli"])[0])
        recommendations = self.policy.app_resource_recommendations()
        recommendation = recommendations.get(app_choice, recommendations["ubuntu-cli"])
        h = formdata.get("hours", [recommendation["hours"]])[0]
        runtime, runtime_line = runtime_from_hours_choice(h)
        try:
            g = int(formdata.get("gpu", [recommendation["gpu"]])[0] or 0)
        except ValueError:
            g = 0
        g = 1 if g > 0 else 0
        memory = str(formdata.get("mem", [recommendation["memory"]])[0]).strip()
        if not memory.upper().endswith("G"):
            memory = f"{memory}G"
        nprocs = str(formdata.get("cpu", [recommendation["cpu"]])[0])
        execution = ExecutionRequest(app_choice, nprocs, memory, runtime, g > 0)
        execution.require_resources(self.policy, self.resources.slurm_free_resources())
        return {
            "nprocs": nprocs,
            "memory": memory,
            "runtime": runtime,
            "runtime_line": runtime_line,
            "gres_line": "",
            "gpu_visibility_line": "" if g > 0 else 'export CUDA_VISIBLE_DEVICES=""',
            "gpu": str(g),
            "app_choice": app_choice,
            "job_name": "jhub-openwebui" if app_choice == "open-webui" else "jhub-app",
            "openwebui_version": self.settings.openwebui_version
            if app_choice == "open-webui"
            else "",
            "ubuntu_version": self.settings.jupyter_ubuntu_version
            if app_choice != "open-webui"
            else "",
        }


class StopUserOpenWebuiJobsUseCase:
    def __init__(
        self,
        *,
        user_jobs: UserJobs,
        gateway: LlmManagementGateway,
        commands: CommandRunner,
    ):
        self.user_jobs = user_jobs
        self.gateway = gateway
        self.commands = commands

    async def execute(self, username: str) -> str | None:
        """管理中spawnerと残存Slurm jobの両方からOpen WebUIを停止する。

        Args:
            username: 対象のLinuxユーザー名。

        Returns:
            正常ならNone、停止に失敗した場合はエラーメッセージ。
        """
        errors = []
        try:
            server_names = self.user_jobs.active_servers(username, "open-webui")
        except Exception as exc:
            server_names = []
            errors.append(self.gateway.safe_litellm_error(exc))
        for server_name in server_names:
            try:
                await self.user_jobs.stop_server(username, server_name)
            except Exception as exc:
                errors.append(self.gateway.safe_litellm_error(exc))
        # Hubに残っていないジョブも、Open WebUI専用の名前で回収する。
        queue = await asyncio.to_thread(
            self.commands.run,
            ["squeue", "-h", "-u", username, "-n", "jhub-openwebui", "-o", "%A"],
        )
        if queue.returncode == 0:
            job_ids = [
                line.strip()
                for line in queue.stdout.splitlines()
                if line.strip().isdigit()
            ]
            if job_ids:
                canceled = await asyncio.to_thread(
                    self.commands.run, ["scancel", *job_ids]
                )
                if canceled.returncode != 0:
                    errors.append(
                        (canceled.stderr or canceled.stdout or "scancel failed").strip()
                    )
        else:
            errors.append((queue.stderr or queue.stdout or "squeue failed").strip())
        if errors:
            joined = "; ".join(
                (self.gateway.safe_litellm_error(error) for error in errors)
            )
            self.gateway.log_litellm_action(
                "openwebui_stop", username, "failed", joined
            )
            return joined
        self.gateway.log_litellm_action("openwebui_stop", username, "ok")
        return None


class PrepareOpenwebuiLaunchUseCase:
    def __init__(
        self,
        *,
        ensure_openwebui_key: EnsureOpenWebuiKeyUseCase,
    ):
        self.ensure_openwebui_key = ensure_openwebui_key

    async def execute(self, username, another_active):
        """同時起動制限を確認し、Open WebUIへ渡す専用キーを準備する。"""
        ExecutionRequest.require_openwebui_slot(username, another_active)
        key, error = await asyncio.to_thread(
            self.ensure_openwebui_key.execute, username
        )
        if error:
            raise ValueError(f"Open WebUI を起動できません: {error}")
        return {"OPENWEBUI_LITELLM_API_KEY": key or ""}
