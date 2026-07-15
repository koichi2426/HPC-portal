"""リソース状態APIのレスポンスSchemaを定義する。"""

from pydantic import BaseModel, ConfigDict


class HpcGpuProcess(BaseModel):
    """GPUを使用しているホストプロセス。"""

    model_config = ConfigDict(extra="ignore")

    pid: int
    name: str
    username: str


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
    mem_status: str
    disk_available: float
    disk_available_gb: float
    disk_total_gb: float
    disk_status: str
    gpu_max: int
    gpu_processes: list[HpcGpuProcess]
    gpu_process_count: int
    gpu_processes_available: bool
    updated_at: float | None = None
