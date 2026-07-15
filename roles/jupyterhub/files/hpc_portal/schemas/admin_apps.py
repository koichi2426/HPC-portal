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
    max_rss_bytes: int | None
    max_rss_label: str


class HpcAdminAppsResponse(BaseModel):
    """管理者向け起動中アプリ一覧APIのレスポンス。"""

    model_config = ConfigDict(extra="ignore")

    apps: list[HpcAdminApp]
    error: str
    updated_at: float
