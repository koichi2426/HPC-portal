"""API公開設定の所有者と、希望状態・実際の状態。"""

from dataclasses import dataclass
from enum import StrEnum

from hpc_portal.domain.external_api.credentials import ApiOwner


class PublicationState(StrEnum):
    """接続確認や外部設定を反映した、現在の公開状態。"""

    CHECKING = "checking"
    CONFIGURING = "configuring"
    PUBLISHED = "published"
    UNPUBLISHED = "unpublished"
    DISABLED = "disabled"
    DISCONNECTED = "disconnected"
    ERROR = "error"


class DesiredPublicationState(StrEnum):
    """利用者が指定した状態。同期処理は現在の状態をここへ近づける。"""

    PUBLISHED = "published"
    UNPUBLISHED = "unpublished"
    DELETED = "deleted"


@dataclass(frozen=True)
class ApiTarget:
    uid: int
    port: int

    def require_owner(self, owner: ApiOwner) -> None:
        """待受ポートの所有者とポート番号の範囲を確認する。

        Args:
            owner: 照合するAPIの所有者情報。

        Raises:
            ValueError: 接続先の所有者が不一致、またはポートが公開可能な範囲外の場合。
        """
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
        """現在の状態と利用者の希望が共に公開済みか確認する。

        Returns:
            APIへ転送できる場合はTrue。
        """
        return (
            self.state == PublicationState.PUBLISHED
            and self.desired == DesiredPublicationState.PUBLISHED
        )

    def require_owner(self, owner: ApiOwner) -> None:
        """登録された所有者以外からの操作を拒否する。

        Args:
            owner: 照合するAPIの所有者情報。

        Raises:
            ValueError: 登録された所有者と操作したユーザーが一致しない場合。
        """
        if self.owner != owner:
            raise ValueError("API の登録が見つかりません")

    @property
    def needs_configuration(self) -> bool:
        """外部公開設定の作成または再試行が必要か確認する。

        Returns:
            公開設定の再確認が必要な場合はTrue。
        """
        return self.state in {
            PublicationState.CHECKING,
            PublicationState.CONFIGURING,
            PublicationState.ERROR,
            PublicationState.DISABLED,
        }

    def begin_publication(self) -> None:
        """接続先の所有者を確認し、公開希望と接続確認中の状態を設定する。

        Raises:
            ValueError: 接続先の所有者またはポートが不正な場合。
        """
        self.target.require_owner(self.owner)
        self.desired = DesiredPublicationState.PUBLISHED
        self.state = PublicationState.CHECKING

    def begin_configuration(self) -> None:
        """接続確認中からCloudflare設定中へ進める。

        Raises:
            ValueError: 接続確認中以外の状態から設定を開始する場合。
        """
        if self.state != PublicationState.CHECKING:
            raise ValueError("接続確認後に公開設定へ進めてください")
        self.state = PublicationState.CONFIGURING

    def complete_publication(self) -> None:
        """公開希望と現在の状態を確認し、公開完了へ進める。

        Raises:
            ValueError: 公開希望が取り消された、または公開準備が未完了の場合。
        """
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
        """公開を直ちに拒否し、公開停止または登録削除の希望を保存する。

        Args:
            delete: 公開停止に加えて登録削除を希望する場合はTrue。
        """
        self.desired = (
            DesiredPublicationState.DELETED
            if delete
            else DesiredPublicationState.UNPUBLISHED
        )
        self.state = PublicationState.UNPUBLISHED

    def fail(self) -> None:
        """公開処理が失敗した状態へ変更する。"""
        self.state = PublicationState.ERROR

    def disable(self) -> None:
        """認証情報を使えず公開できない状態へ変更する。"""
        self.state = PublicationState.DISABLED

    def disconnect(self) -> None:
        """登録時の接続先へ到達できない状態へ変更する。"""
        self.state = PublicationState.DISCONNECTED
