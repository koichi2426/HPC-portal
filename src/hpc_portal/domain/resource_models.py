"""リソース状態APIのレスポンスSchemaを定義する。"""

from pydantic import BaseModel, ConfigDict


class HpcGpuProcess(BaseModel):
    """GPUを使用しているホストプロセス。"""

    model_config = ConfigDict(extra="ignore")

    pid: int
    name: str
    username: str
    memory_mb: int | None = None


class HpcResourceSnapshot(BaseModel):
    """画面へ返すCPU・メモリ・ストレージ・GPU状態。"""

    model_config = ConfigDict(extra="ignore")

    cpu_available: float
    cpu_available_count: float
    cpu_total: int
    cpu_status: str
    mem_available: float
    mem_available_gb: float
    mem_used_gb: float
    mem_total_gb: float
    mem_gpu_used_gb: float
    mem_status: str
    mem_slurm_available: float
    mem_slurm_available_gb: float
    mem_slurm_used_gb: float
    mem_slurm_total_gb: float
    mem_slurm_status: str
    disk_available: float
    disk_available_gb: float
    disk_total_gb: float
    disk_status: str
    gpu_max: int
    gpu_available: float
    gpu_available_count: int
    gpu_status: str
    gpu_processes: list[HpcGpuProcess]
    gpu_process_count: int
    gpu_processes_available: bool
    updated_at: float | None = None


def format_storage_bytes(value: int) -> str:
    """ストレージ使用量を管理画面向けの短い表記にする。

    Args:
        value: ストレージ使用量のバイト数。

    Returns:
        単位を付けて整形した使用量。
    """
    size = float(max(value, 0))
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def resource_status(available_pct):
    """空き率を画面表示用の混雑度へ変換する。

    Args:
        available_pct: 0〜100の空き率。

    Returns:
        余裕あり、やや混雑、逼迫のいずれか。
    """
    if available_pct >= 50:
        return "余裕あり"
    if available_pct >= 25:
        return "やや混雑"
    return "逼迫"


def memory_display_label(memory: str) -> str:
    """Slurm形式のメモリ値を画面表示用のGB表記へ変換する。

    Args:
        memory: ``32G``または``32GB``形式のメモリ値。

    Returns:
        ``32 GB``形式の表示値。
    """
    normalized = str(memory).strip().upper()
    if normalized.endswith("GB"):
        normalized = normalized[:-2]
    elif normalized.endswith("G"):
        normalized = normalized[:-1]
    return f"{normalized} GB"
