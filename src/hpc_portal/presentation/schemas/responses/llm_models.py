"""LiteLLMモデル一覧をポータル表示用に正規化するSchemaを定義する。"""

from pydantic import BaseModel, ConfigDict


class HpcLlmModel(BaseModel):
    """利用者へ公開するLiteLLMモデル情報。"""

    model_config = ConfigDict(extra="allow")

    id: str
    owned_by: str = ""
