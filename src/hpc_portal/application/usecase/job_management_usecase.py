"""ジョブ作成の構成確認と、利用者のジョブ停止手順。"""

import asyncio

from hpc_portal.application.ports.job_management_ports import CommandRunner, UserJobs
from hpc_portal.domain.errors import UseCaseError
from hpc_portal.domain.job_models import JobSettings, runtime_from_hours_choice
from hpc_portal.domain.resource_models import memory_display_label


class JobManagementUseCase:
    def __init__(
        self,
        resources,
        commands: CommandRunner,
        users: UserJobs,
        settings: JobSettings,
        llm,
    ):
        self.resources = resources
        self.commands = commands
        self.users = users
        self.settings = settings
        self.llm = llm

    def app_resource_recommendations(self) -> dict[str, dict[str, str]]:
        """アプリ選択時に適用する推奨リソースと案内文を返す。

        Returns:
            アプリ内部名をキーとする推奨リソース設定。
        """
        return {
            "ubuntu-cli": {
                "label": "JupyterLab",
                "cpu": "2",
                "memory": "4",
                "memory_label": "4 GB",
                "gpu": "0",
                "hours": "2",
                "hours_label": "2時間",
                "summary": "コード編集や軽いPython処理向けです。",
                "guidance": (
                    "データ分析は4 vCPU・8 GB、AI処理は8 vCPU・32 GB・GPU 1が目安です。"
                ),
            },
            "open-webui": {
                "label": "Open WebUI",
                "cpu": "2",
                "memory": "4",
                "memory_label": "4 GB",
                "gpu": "0",
                "hours": "2",
                "hours_label": "2時間",
                "summary": "通常のチャットやWeb検索に十分です。",
                "guidance": "モデル推論は共有Ollamaが担当します。",
            },
            "shared-ollama": {
                "label": "Ollama",
                "cpu": self.settings.ollama_default_cpus,
                "memory": self.settings.ollama_default_memory,
                "memory_label": memory_display_label(
                    self.settings.ollama_default_memory
                ),
                "gpu": "1",
                "hours": "unlimited",
                "hours_label": "無制限",
                "summary": "共有モデルの推論向けです。",
                "guidance": (
                    "大きなモデルや同時利用が増えた場合はRAMを調整してください。"
                ),
            },
        }

    def parse_requested_memory_gb(self, memory: str) -> float | None:
        """フォームのメモリ指定(例: 40G)をGBへ変換する。

        Args:
            memory: ``40G`` や ``4096M`` のようなSlurmのメモリ表記。

        Returns:
            GB単位の要求量。解釈できなければNone。
        """
        raw = str(memory or "").strip().upper()
        if not raw:
            return None
        multipliers = {"K": 1 / 1024**2, "M": 1 / 1024, "G": 1.0, "T": 1024.0}
        suffix = raw[-1]
        factor = multipliers.get(suffix)
        number = raw[:-1] if factor is not None else raw
        try:
            return float(number) * (factor if factor is not None else 1.0)
        except (TypeError, ValueError):
            return None

    def requested_resources_error(self, nprocs, memory) -> str:
        """要求リソースがノードの空きを超えていないか投入前に検証する。

        Slurmの空きを超える要求は PENDING のまま start_timeout(5分)に達して失敗する。
        利用者は理由の分からないまま5分待たされるため、sbatchへ渡す前に弾く。

        Args:
            nprocs: 要求vCPU数。
            memory: 要求メモリ（Slurm表記）。

        Returns:
            起動できない理由。起動できる場合、またはSlurmへ問い合わせられない場合は空文字列。
        """
        free = self.resources.slurm_free_resources()
        if not free:
            # Slurmへ問い合わせられないときは判断材料が無いため通す（従来どおりの挙動）。
            return ""
        reasons = []
        try:
            requested_cpu = int(str(nprocs).strip() or 0)
        except (TypeError, ValueError):
            requested_cpu = 0
        available_cpu = float(free.get("cpu_available_count") or 0)
        if requested_cpu > 0 and requested_cpu > available_cpu:
            reasons.append(
                f"vCPU {requested_cpu} を要求していますが、空きは {available_cpu:.0f} です"
            )
        requested_mem_gb = self.parse_requested_memory_gb(memory)
        available_mem_gb = float(free.get("mem_available_mb") or 0) / 1024
        if requested_mem_gb and requested_mem_gb > available_mem_gb:
            reasons.append(
                f"メモリ {requested_mem_gb:.0f} GB を要求していますが、"
                f"空きは {available_mem_gb:.1f} GB です"
            )
        # GPUはGRES予約せず全ジョブで共有するため、枚数の空き判定はしない。
        # 統合メモリ構成ではGPUの確保分もメモリから出ていくため、上のメモリ判定が効く。
        if not reasons:
            return ""
        return (
            "現在の空きリソースでは起動できません。"
            + "、".join(reasons)
            + "。構成を小さくするか、実行中のアプリが終了してからお試しください。"
        )

    def options_from_form(self, formdata):
        """フォーム入力をSpawnerのuser_optionsへ変換する。

        Args:
            formdata: JupyterHubが渡す複数値形式のフォーム辞書。

        Returns:
            検証・正規化済みのuser_options。
        """
        app_choice = str(formdata.get("app_choice", ["ubuntu-cli"])[0])
        recommendations = self.app_resource_recommendations()
        recommendation = recommendations.get(app_choice, recommendations["ubuntu-cli"])
        h = formdata.get("hours", [recommendation["hours"]])[0]
        runtime, runtime_line = runtime_from_hours_choice(h)
        try:
            g = int(formdata.get("gpu", [recommendation["gpu"]])[0] or 0)
        except ValueError:
            g = 0
        # GPUはGRES予約せず全員で共有するため、値は「使う/使わない」の2値。
        # HPC_GPU_COUNT でクランプしない。この値は slurm ロールが set_fact する
        # slurm_effective_gpu_count に由来し、--tags jupyterhub のようにslurmロールを
        # 飛ばすデプロイでは 0 になる。枚数として扱うと、その場合に利用者の選択が
        # 黙って 0 へ潰され、CUDA_VISIBLE_DEVICES="" でGPUが使えなくなる。
        g = 1 if g > 0 else 0
        memory = str(formdata.get("mem", [recommendation["memory"]])[0]).strip()
        if not memory.upper().endswith("G"):
            memory = f"{memory}G"
        nprocs = str(formdata.get("cpu", [recommendation["cpu"]])[0])
        error = self.requested_resources_error(nprocs, memory)
        if error:
            raise UseCaseError(error)
        return {
            "nprocs": nprocs,
            "memory": memory,
            "runtime": runtime,
            "runtime_line": runtime_line,
            # GRES予約はしない。ノードのGPUは1枚しかなく、予約すると2人目以降が
            # 永久にPENDINGになる。cgroup.conf で ConstrainDevices=no を明示しており、
            # 起動スクリプトは常に apptainer exec --nv で実行するため、予約が無くても
            # GPUは使える（共有Ollamaと同じ方式）。
            "gres_line": "",
            # 「GPUなし」を選んだジョブがGPUメモリを確保しないよう、CUDAから隠す。
            # 予約で締め出せない以上、ここが唯一の「使わない」の表明手段になる。
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

    async def stop_user_openwebui_servers(self, username: str) -> str | None:
        """管理中spawnerと残存Slurm jobの両方からOpen WebUIを停止する。

        Args:
            username: 対象のLinuxユーザー名。

        Returns:
            正常ならNone、停止に失敗した場合はエラーメッセージ。
        """
        errors = []
        try:
            target_user = self.users.find_user(username)
        except Exception as exc:
            target_user = None
            errors.append(self.llm.safe_litellm_error(exc))
        if target_user is not None:
            for server_name, spawner in list(target_user.spawners.items()):
                if (
                    str(
                        (getattr(spawner, "user_options", None) or {}).get("app_choice")
                        or ""
                    )
                    != "open-webui"
                ):
                    continue
                if not (
                    getattr(spawner, "active", False)
                    or getattr(spawner, "pending", None)
                ):
                    continue
                try:
                    await target_user.stop(server_name)
                except Exception as exc:
                    errors.append(self.llm.safe_litellm_error(exc))

        # Hub stateに残っていないjobも、Open WebUI専用job名で回収する。
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
            joined = "; ".join(self.llm.safe_litellm_error(error) for error in errors)
            self.llm.log_litellm_action("openwebui_stop", username, "failed", joined)
            return joined
        self.llm.log_litellm_action("openwebui_stop", username, "ok")
        return None
