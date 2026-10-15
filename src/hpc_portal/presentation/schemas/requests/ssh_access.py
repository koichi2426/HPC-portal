"""本人のSSH接続情報を管理する操作入力。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class SshOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["reveal", "download", "issue", "rotate", "revoke"]
