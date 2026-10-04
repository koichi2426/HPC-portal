"""推論モデルの名前と、既存deploymentを扱うためのルール。"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelName:
    value: str

    def validation_error(self) -> str | None:
        if not self.value:
            return "モデル名が必要です"
        if re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", self.value) is None:
            return "モデル名に使用できない文字が含まれています"
        return None


@dataclass(frozen=True)
class ModelPullProgress:
    state: str

    @property
    def completed(self) -> bool:
        return self.state == "completed"

    @property
    def terminal(self) -> bool:
        return self.state in {"failed", "busy", "cancelled", "cancelled_cleanup_failed"}
