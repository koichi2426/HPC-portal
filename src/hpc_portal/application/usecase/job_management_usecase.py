"""ジョブの準備・利用権限確認・停止を行う操作手順。"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

from hpc_portal.application.ports.job_management_ports import CommandRunner, UserJobs
from hpc_portal.application.ports.llm_gateway_ports import LlmManagementGateway
from hpc_portal.application.ports.resource_monitoring_ports import ResourceInventory
from hpc_portal.domain.errors import UseCaseError
from hpc_portal.domain.jobs.execution_policy import ExecutionPolicy
from hpc_portal.domain.jobs.execution_request import ExecutionRequest
from hpc_portal.domain.jobs.settings import JobSettings, runtime_from_hours_choice

# Spawnerで起動するアプリ。共有Ollamaは起動フォームから別画面へ移る。
SPAWNABLE_APPS = ("ubuntu-cli", "open-webui")
# 利用者が指定できる項目。#SBATCH行などは受け取らず、サーバー側で組み立てる。
USER_CHOICE_KEYS = ("app_choice", "nprocs", "memory", "hours", "gpu")


def positive_int_text(value, label):
    """利用者の指定を、#SBATCH行へ埋め込める正の整数表記に揃える。

    Args:
        value: 利用者が指定した数値。
        label: エラー表示に使う項目名。

    Returns:
        先頭のゼロを除いた正の整数の文字列。

    Raises:
        UseCaseError: 正の整数でない場合。
    """
    raw = str(value).strip()
    if not re.fullmatch(r"[0-9]+", raw) or int(raw) < 1:
        raise UseCaseError(f"{label}は1以上の整数で指定してください")
    return str(int(raw))


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
        """この操作に必要な接続先と処理の依存を保持する。

        Args:
            policy: ジョブの入力条件とリソース割当を検証するルール。
            resources: OS・Slurm・GPUのリソース情報を取得する接続先。
            settings: アプリの起動条件・リソース上限・実行時間・公開先の設定。
        """
        self.policy = policy
        self.resources = resources
        self.settings = settings

    def execute(self, formdata):
        """フォーム入力をSpawnerのuser_optionsへ変換する。

        Args:
            formdata: JupyterHubが渡す複数値形式のフォーム辞書。

        Returns:
            検証・正規化済みのuser_options。

        Raises:
            UseCaseError: アプリ・CPU・メモリの指定が不正、または空きを超える場合。
        """
        fields = {
            "app_choice": "app_choice",
            "nprocs": "cpu",
            "memory": "mem",
            "hours": "hours",
            "gpu": "gpu",
        }
        return self.prepare(
            {
                key: formdata[name][0]
                for key, name in fields.items()
                if formdata.get(name)
            }
        )

    def revalidate(self, user_options):
        """APIや保存済みの値も含め、起動直前のuser_optionsを検証し直す。

        Args:
            user_options: フォーム・APIから渡された、または前回保存された設定。

        Returns:
            利用者の指定項目だけから組み立て直したuser_options。

        Raises:
            UseCaseError: アプリ・CPU・メモリの指定が不正、または空きを超える場合。
        """
        return self.prepare(
            {key: user_options[key] for key in USER_CHOICE_KEYS if key in user_options}
        )

    def prepare(self, choices):
        """利用者の指定を検証し、#SBATCH行を含む起動設定をサーバー側で組み立てる。

        Args:
            choices: app_choice・nprocs・memory・hours・gpuのうち指定された項目。

        Returns:
            検証・正規化済みのuser_options。

        Raises:
            UseCaseError: アプリ・CPU・メモリの指定が不正、または空きを超える場合。
        """
        app_choice = str(choices.get("app_choice") or "ubuntu-cli")
        if app_choice not in SPAWNABLE_APPS:
            raise UseCaseError("起動できないアプリが指定されています")
        recommendation = self.policy.app_resource_recommendations()[app_choice]
        nprocs = positive_int_text(choices.get("nprocs", recommendation["cpu"]), "vCPU")
        memory_gb = positive_int_text(
            str(choices.get("memory", recommendation["memory"]))
            .strip()
            .upper()
            .removesuffix("G"),
            "RAM",
        )
        memory = f"{memory_gb}G"
        runtime, runtime_line = runtime_from_hours_choice(
            choices.get("hours", recommendation["hours"])
        )
        hours = (
            "unlimited" if runtime == "UNLIMITED" else str(int(runtime.split(":")[0]))
        )
        try:
            g = int(choices.get("gpu", recommendation["gpu"]) or 0)
        except (TypeError, ValueError):
            g = 0
        g = 1 if g > 0 else 0

        # 表記を揃えてから、実際の空きリソースに対して要求を検証する。
        execution = ExecutionRequest(app_choice, nprocs, memory, runtime, g > 0)
        execution.require_resources(self.policy, self.resources.slurm_free_resources())

        return {
            "nprocs": nprocs,
            "memory": memory,
            "runtime": runtime,
            "hours": hours,
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
        """この操作に必要な接続先と処理の依存を保持する。

        Args:
            user_jobs: 利用者のHub管理ジョブを取得・停止する接続先。
            gateway: LLMの管理操作・応答解析・排他制御を提供する接続先。
            commands: OSコマンドを実行する接続先。
        """
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
        """この操作に必要な接続先と処理の依存を保持する。

        Args:
            ensure_openwebui_key: 有効なOpen WebUI専用キーを取得・発行する操作。
        """
        self.ensure_openwebui_key = ensure_openwebui_key

    async def execute(self, username, another_active):
        """同時起動制限を確認し、Open WebUIへ渡す専用キーを準備する。

        Args:
            username: 対象のLinuxユーザー名。
            another_active: 同じユーザーのOpen WebUIが既に稼働しているか。

        Returns:
            Open WebUIの起動時に渡す専用キーの環境変数。

        Raises:
            ValueError: 同時起動制限に該当する場合、またはキーを準備できない場合。
        """
        ExecutionRequest.require_openwebui_slot(username, another_active)

        key, error = await asyncio.to_thread(
            self.ensure_openwebui_key.execute, username
        )
        if error:
            raise ValueError(f"Open WebUI を起動できません: {error}")

        return {"OPENWEBUI_LITELLM_API_KEY": key or ""}
