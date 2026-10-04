"""ジョブ実行時間の選択値とSlurm表記。"""

from dataclasses import dataclass


def runtime_from_hours_choice(hours_value: str) -> tuple[str, str]:
    """起動フォームの hours 値から (runtime, #SBATCH 行) を生成する

    Args:
        hours_value: フォームで選択された実行時間。

    Returns:
        Slurm形式の実行時間と、対応する``#SBATCH --time``行の組。
    """
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
