"""API公開設定の所有者と、希望状態・実際の状態。"""

from dataclasses import dataclass
from enum import StrEnum

from hpc_portal.domain.external_api.credentials import ApiOwner


class PublicationState(StrEnum):
    CHECKING = "checking"
    CONFIGURING = "configuring"
    PUBLISHED = "published"
    UNPUBLISHED = "unpublished"
    DISABLED = "disabled"
    DISCONNECTED = "disconnected"
    ERROR = "error"


class DesiredPublicationState(StrEnum):
    PUBLISHED = "published"
    UNPUBLISHED = "unpublished"
    DELETED = "deleted"


@dataclass(frozen=True)
class ApiTarget:
    uid: int
    port: int

    def require_owner(self, owner: ApiOwner) -> None:
        if self.uid != owner.uid:
            raise ValueError("本人が起動したAPIだけを公開できます")
        if not 1024 <= self.port <= 65535:
            raise ValueError("公開ポートが不正です")


@dataclass
class ApiPublication:
    owner: ApiOwner
    name: str
    target: ApiTarget
    state: PublicationState = PublicationState.CHECKING
    desired: DesiredPublicationState = DesiredPublicationState.PUBLISHED

    @property
    def available(self) -> bool:
        return (
            self.state == PublicationState.PUBLISHED
            and self.desired == DesiredPublicationState.PUBLISHED
        )

    def require_owner(self, owner: ApiOwner) -> None:
        if self.owner != owner:
            raise ValueError("API の登録が見つかりません")

    @property
    def needs_configuration(self) -> bool:
        return self.state in {
            PublicationState.CHECKING,
            PublicationState.CONFIGURING,
            PublicationState.ERROR,
            PublicationState.DISABLED,
        }

    def begin_publication(self) -> None:
        self.target.require_owner(self.owner)
        self.desired = DesiredPublicationState.PUBLISHED
        self.state = PublicationState.CHECKING

    def begin_configuration(self) -> None:
        if self.state != PublicationState.CHECKING:
            raise ValueError("接続確認後に公開設定へ進めてください")
        self.state = PublicationState.CONFIGURING

    def complete_publication(self) -> None:
        if self.desired != DesiredPublicationState.PUBLISHED:
            raise ValueError("公開停止済みのAPIは公開完了にできません")
        if self.state not in {
            PublicationState.CONFIGURING,
            PublicationState.DISCONNECTED,
            PublicationState.PUBLISHED,
        }:
            raise ValueError("公開準備が完了していません")
        self.state = PublicationState.PUBLISHED

    def stop(self, *, delete: bool = False) -> None:
        self.desired = (
            DesiredPublicationState.DELETED
            if delete
            else DesiredPublicationState.UNPUBLISHED
        )
        self.state = PublicationState.UNPUBLISHED

    def fail(self) -> None:
        self.state = PublicationState.ERROR

    def disable(self) -> None:
        self.state = PublicationState.DISABLED

    def disconnect(self) -> None:
        self.state = PublicationState.DISCONNECTED
