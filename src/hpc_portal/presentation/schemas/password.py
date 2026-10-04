"""本人用パスワード変更APIの入力Schemaを定義する。"""

from pydantic import BaseModel, ConfigDict


class HpcPasswordChangeRequest(BaseModel):
    """本人が入力する現在・新規・確認用パスワード。"""

    model_config = ConfigDict(extra="ignore", strict=True)

    current_password: str
    new_password: str
    confirm_password: str
