"""認証情報・API公開状態の保存契約。秘密値の保管形式は実装側が扱う。"""

from typing import Protocol

from hpc_portal.domain.external_api.credentials import ApiCredentials
from hpc_portal.domain.external_api.publication import ApiPublication


class ApiCredentialRepository(Protocol):
    def load_credentials(self, username: str) -> ApiCredentials | None:
        """ユーザーの認証情報の所有者と状態を読み込む。

        Args:
            username: 対象のLinuxユーザー名。

        Returns:
            認証情報のdomainモデル。未登録ならNone。
        """
        ...

    def save_credentials(self, credentials: ApiCredentials) -> None:
        """認証情報の所有者と状態を保存する。

        Args:
            credentials: 所有者・有効状態・発行状態を持つ認証情報のdomainモデル。
        """
        ...


class ApiPublicationRepository(Protocol):
    def load_publication(self, username: str, name: str) -> ApiPublication | None:
        """ユーザーとAPI名を指定して公開状態を読み込む。

        Args:
            username: 対象のLinuxユーザー名。
            name: ユーザーが登録したAPIの識別名。

        Returns:
            API公開設定のdomainモデル。未登録ならNone。
        """
        ...

    def save_publication(self, publication: ApiPublication) -> None:
        """API公開設定の状態と希望状態を保存する。

        Args:
            publication: 保存するAPI公開設定のdomainモデル。
        """
        ...
