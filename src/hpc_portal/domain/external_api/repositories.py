"""認証情報・API公開状態の保存契約。秘密値の保管形式は実装側が扱う。"""

from typing import Protocol

from hpc_portal.domain.external_api.credentials import ApiCredentials
from hpc_portal.domain.external_api.publication import ApiPublication


class ApiCredentialRepository(Protocol):
    def load_credentials(self, username: str) -> ApiCredentials | None: ...
    def save_credentials(self, credentials: ApiCredentials) -> None: ...


class ApiPublicationRepository(Protocol):
    def load_publication(self, username: str, name: str) -> ApiPublication | None: ...
    def save_publication(self, publication: ApiPublication) -> None: ...
