"""検出済みのAPIを公開する入力。任意の起動コマンドや接続先URLは受け付けない。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Registration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,47}$")
    display_name: str = Field(default="", max_length=80)
    candidate: str = Field(default="", max_length=512)
    port: int | None = Field(default=None, ge=1024, le=65535)
    health_path: str = Field(default="", max_length=512)

    @field_validator("health_path")
    @classmethod
    def health_value(cls, value):
        """確認パスがAPI内の安全な相対パスか検証する。

        Args:
            value: 入力されたAPI内の動作確認パス。

        Returns:
            検証済みの確認パス。

        Raises:
            ValueError: API内の安全な絶対パス形式でない場合。
        """
        if value and (
            not value.startswith("/")
            or value.startswith("//")
            or any(c in value for c in "\r\n\x00?#")
        ):
            raise ValueError("確認パスは / から始まるパスを指定してください")
        return value

    @field_validator("display_name")
    @classmethod
    def display_value(cls, value):
        """表示名の制御文字を拒否し、前後の空白を除く。

        Args:
            value: 入力されたAPIの表示名。

        Returns:
            正規化した表示名。

        Raises:
            ValueError: 表示名に制御文字が含まれる場合。
        """
        if any(ord(c) < 32 for c in value):
            raise ValueError("表示名に制御文字は使用できません")
        return value.strip()


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal[
        "reveal",
        "download",
        "rotate_cloudflare",
        "rotate_jupyterhub",
        "publish",
        "unpublish",
        "delete",
    ]
    name: str = Field(default="", pattern=r"^(?:[a-z][a-z0-9-]{0,47})?$")
