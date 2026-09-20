"""管理者向け起動中アプリAPIのレスポンスSchemaを定義する。"""

from pydantic import BaseModel, ConfigDict


class HpcAdminApp(BaseModel):
    """Slurmで起動しているポータルアプリの表示情報。"""

    model_config = ConfigDict(extra="ignore")

    job_id: str
    username: str
    display_name: str
    app: str
    state: str
    state_label: str
    cpus: str
    memory: str
    gpus: int
    elapsed: str
    started_at: str
    cpu_memory_bytes: int | None = None
    cpu_memory_label: str = "取得不可"
    gpu_memory_bytes: int | None = None
    gpu_memory_label: str = "—"
    memory_used_bytes: int | None = None
    memory_used_label: str = "取得不可"
    memory_limit_bytes: int | None = None
    memory_usage_ratio: float | None = None
    memory_overuse_level: str = ""
    memory_overuse_label: str = ""


class HpcAdminAppsResponse(BaseModel):
    """管理者向け起動中アプリ一覧APIのレスポンス。"""

    model_config = ConfigDict(extra="ignore")

    apps: list[HpcAdminApp]
    error: str
    updated_at: float
