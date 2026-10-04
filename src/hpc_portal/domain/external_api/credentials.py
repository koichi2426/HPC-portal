"""個人別API認証情報の所有者と発行・失効の状態遷移。"""

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True)
class ApiOwner:
    username: str
    uid: int
    hub_user_id: int

    def matches(self, uid: int, hub_user_id: int) -> bool:
        return self.uid == uid and self.hub_user_id == hub_user_id


class CredentialState(StrEnum):
    ISSUING = "issuing"
    READY = "ready"
    ROTATING_CLOUDFLARE = "rotating_cloudflare"
    ROTATING_JUPYTERHUB = "rotating_jupyterhub"
    REVOKING = "revoking"
    DISABLED = "disabled"


@dataclass
class ApiCredentials:
    owner: ApiOwner
    enabled: bool = True
    state: CredentialState = CredentialState.ISSUING

    @property
    def available(self) -> bool:
        return self.enabled and self.state == CredentialState.READY

    def require_available(self) -> None:
        if not self.available:
            raise ValueError("外部 API の接続情報が準備できていません")

    def begin_rotation(self, kind: str) -> None:
        if kind not in {"cloudflare", "jupyterhub"}:
            raise ValueError("再発行対象が不正です")
        self.require_available()
        self.state = CredentialState("rotating_" + kind)

    def complete_issuance(self) -> None:
        if not self.enabled or self.state in {
            CredentialState.REVOKING,
            CredentialState.DISABLED,
        }:
            raise ValueError("利用停止中の認証情報は発行完了にできません")
        self.state = CredentialState.READY

    def begin_revocation(self) -> None:
        self.enabled = False
        self.state = CredentialState.REVOKING

    def complete_revocation(self) -> None:
        if self.state != CredentialState.REVOKING:
            raise ValueError("失効開始後に失効完了へ進めてください")
        self.enabled = False
        self.state = CredentialState.DISABLED

    def require_reenableable(self) -> None:
        if self.state != CredentialState.DISABLED:
            raise ValueError("失効処理が完了していません")
