"""本人用LLM API操作の入力Schemaを定義する。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator


class HpcLlmApiRequest(BaseModel):
    """本人用LLM API Key操作リクエスト。"""

    model_config = ConfigDict(extra="ignore", strict=True)

    action: Literal["regenerate"]

    @field_validator("action", mode="before")
    @classmethod
    def normalize_action(cls, value: object) -> object:
        """操作名の前後空白を除いて小文字へ変換する。

        Args:
            value: 正規化前の操作名。

        Returns:
            文字列の場合は正規化済みの操作名。
        """
        return value.strip().lower() if isinstance(value, str) else value
