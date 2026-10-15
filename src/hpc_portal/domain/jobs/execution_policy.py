"""実行要求と空きリソースの判断。"""

from hpc_portal.domain.resources.snapshot import memory_display_label


class ExecutionPolicy:
    def __init__(self, settings):
        """ジョブの実行条件を判断する設定を保持する。

        Args:
            settings: アプリの起動条件・リソース上限・実行時間・公開先の設定。
        """
        self.settings = settings

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

    def requested_resources_error(self, nprocs, memory, free) -> str:
        """要求リソースがノードの空きを超えていないか投入前に検証する。

        Slurmの空きを超える要求は PENDING のまま start_timeout(5分)に達して失敗する。
        利用者は理由の分からないまま5分待たされるため、sbatchへ渡す前に弾く。

        Args:
            nprocs: 要求vCPU数。
            memory: 要求メモリ（Slurm表記）。
            free: Slurmが返す割当可能リソース。取得できない場合はNone。

        Returns:
            起動できない理由。起動できる場合、またはSlurmへ問い合わせられない場合は空文字列。
        """
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
