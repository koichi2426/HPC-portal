"""個人別API認証情報の所有者と発行・失効の状態遷移。"""

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True)
class ApiOwner:
    """同名ユーザーの再作成を区別するため、Linux UIDとHubのIDも保持する。"""

    username: str
    uid: int
    hub_user_id: int

    def matches(self, uid: int, hub_user_id: int) -> bool:
        """Linux UIDとHubユーザーIDが同じ所有者を示すか確認する。

        Args:
            uid: 照合するLinuxユーザーのUID。
            hub_user_id: 照合するJupyterHubユーザーのID。

        Returns:
            両方のIDが一致する場合はTrue。
        """
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
    """秘密値を持たず、認証情報を利用できる条件と状態遷移を管理する。"""

    owner: ApiOwner
    enabled: bool = True
    state: CredentialState = CredentialState.ISSUING

    @property
    def available(self) -> bool:
        """認証情報が有効で発行完了しているか確認する。

        Returns:
            認証情報を利用できる場合はTrue。
        """
        return self.enabled and self.state == CredentialState.READY

    def require_available(self) -> None:
        """利用停止中または発行途中の認証情報を拒否する。

        Raises:
            ValueError: 利用停止中、または発行が完了していない場合。
        """
        if not self.available:
            raise ValueError("外部 API の接続情報が準備できていません")

    def begin_rotation(self, kind: str) -> None:
        """利用可能な認証情報を、指定サービスの再発行中へ変更する。

        Args:
            kind: 再発行するサービス。cloudflareまたはjupyterhub。

        Raises:
            ValueError: 認証情報が利用不可、または再発行対象が不正な場合。
        """
        if kind not in {"cloudflare", "jupyterhub"}:
            raise ValueError("再発行対象が不正です")
        self.require_available()
        self.state = CredentialState("rotating_" + kind)

    def complete_issuance(self) -> None:
        """失効中でないことを確認し、認証情報を利用可能へ変更する。

        Raises:
            ValueError: 利用停止中または失効処理中の場合。
        """
        if not self.enabled or self.state in {
            CredentialState.REVOKING,
            CredentialState.DISABLED,
        }:
            raise ValueError("利用停止中の認証情報は発行完了にできません")
        self.state = CredentialState.READY

    def begin_revocation(self) -> None:
        """認証情報を即座に利用不可にし、失効中へ変更する。"""
        self.enabled = False
        self.state = CredentialState.REVOKING

    def complete_revocation(self) -> None:
        """失効中であることを確認し、失効完了へ変更する。

        Raises:
            ValueError: 失効開始前に失効完了へ進める場合。
        """
        if self.state != CredentialState.REVOKING:
            raise ValueError("失効開始後に失効完了へ進めてください")
        self.enabled = False
        self.state = CredentialState.DISABLED

    def require_reenableable(self) -> None:
        """失効が完了した認証情報だけ再有効化を許可する。

        Raises:
            ValueError: 失効が完了していない場合。
        """
        if self.state != CredentialState.DISABLED:
            raise ValueError("失効処理が完了していません")
