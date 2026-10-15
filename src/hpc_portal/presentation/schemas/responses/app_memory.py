"""自分のアプリのメモリ使用状況APIのレスポンスSchemaを定義する。"""

from pydantic import BaseModel, ConfigDict


class HpcAppMemoryUsage(BaseModel):
    """1つのアプリ（Slurmジョブ）のメモリ使用状況。"""

    model_config = ConfigDict(extra="ignore")

    memory_used_label: str = ""
    gpu_memory_label: str = ""
    memory_overuse_level: str = ""
    memory_overuse_label: str = ""


class HpcAppMemoryResponse(BaseModel):
    """ホーム画面のアプリカードを更新するためのレスポンス。"""

    model_config = ConfigDict(extra="ignore")

    apps: dict[str, HpcAppMemoryUsage]
    updated_at: float
