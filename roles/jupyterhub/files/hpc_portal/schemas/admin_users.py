"""管理ユーザー・Ollama操作APIの入力Schemaを定義する。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

HpcAdminAction = Literal[
    "create",
    "display_name",
    "delete",
    "password_regenerate",
    "sudo_enable",
    "sudo_disable",
    "api_enable",
    "api_disable",
    "ollama_register_model",
    "ollama_sync_models",
    "ollama_delete",
    "ollama_start",
    "ollama_stop",
    "ollama_status",
    "ollama_tags",
    "ollama_pull",
    "ollama_pull_cancel",
    "ollama_pull_status",
]


class HpcAdminUsersRequest(BaseModel):
    """管理ユーザー画面から送信される操作リクエスト。"""

    model_config = ConfigDict(extra="ignore", strict=True)

    action: HpcAdminAction
    username: str = ""
    display_name: str = ""
    sudo: bool | None = None
    model: str = ""
    cpus: str = ""
    memory: str = ""

    @field_validator("action", "username", mode="before")
    @classmethod
    def normalize_lower_text(cls, value: object) -> object:
        """操作名とユーザー名を小文字へ正規化する。

        Args:
            value: 正規化前の入力値。

        Returns:
            文字列の場合は空白を除いて小文字化した値。
        """
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("display_name", "model", "cpus", "memory", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> object:
        """文字列入力の前後空白を除去する。

        Args:
            value: 正規化前の入力値。

        Returns:
            文字列の場合は前後空白を除いた値。
        """
        if value is None:
            return ""
        return value.strip() if isinstance(value, str) else value
