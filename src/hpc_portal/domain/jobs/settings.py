"""ジョブ実行時間の選択値とSlurm表記。"""

from dataclasses import dataclass


def runtime_from_hours_choice(hours_value: str) -> tuple[str, str]:
    """フォームの時間指定を、Slurmの実行時間と対応する#SBATCH行へ変換する。"""
    raw = str(hours_value or "").strip().lower()
    if raw in ("unlimited", "infinite", "none", "0"):
        return "UNLIMITED", "#SBATCH --time=UNLIMITED"
    try:
        hours = int(raw)
    except (TypeError, ValueError):
        hours = 8
    hours = max(1, min(hours, 9999))
    runtime = f"{hours:02d}:00:00"
    return runtime, f"#SBATCH --time={runtime}"


@dataclass
class JobSettings:
    openwebui_version: str
    jupyter_ubuntu_version: str
    ollama_default_cpus: str
    ollama_default_memory: str
