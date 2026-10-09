"""個人別APIの認証情報を発行・再発行・失効する操作手順。"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from hpc_portal.application.ports.api_query_ports import ApiQueries
from hpc_portal.application.ports.external_api_ports import (
    ApiConfiguration,
    CloudflareAccess,
    HubTokens,
    RecordStore,
)
from hpc_portal.domain.external_api.repositories import ApiCredentialRepository

if TYPE_CHECKING:
    from hpc_portal.application.usecase.api_publication_usecase import (
        RemoveRemoteApiPublicationUseCase,
        UnpublishUserApisUseCase,
    )


class IssueApiCredentialsUseCase:
    def __init__(
        self,
        *,
        queries: ApiQueries,
        store: RecordStore,
        credential_repository: ApiCredentialRepository,
        hub_tokens: HubTokens,
        cloudflare: CloudflareAccess,
        config: ApiConfiguration,
        revoke_credential_record: RevokeCredentialRecordUseCase,
    ):
        """この操作に必要な接続先と処理の依存を保持する。

        Args:
            queries: 所有者を照合したレコード取得とユーザー単位の排他制御。
            store: 認証情報・公開設定・ポート保護のレコード保存先。
            credential_repository: 認証情報の所有者と状態を読み書きする保存先。
            hub_tokens: JupyterHubのユーザー取得と専用トークン管理を行う接続先。
            cloudflare: Cloudflare Accessのトークンと公開先を管理する接続先。
            config: 外部APIの公開ドメイン・接続制限・保存先などの設定。
            revoke_credential_record: 保存された両サービスのトークンを失効させる操作。
        """
        self.queries = queries
        self.store = store
        self.credential_repository = credential_repository
        self.hub_tokens = hub_tokens
        self.cloudflare = cloudflare
        self.config = config
        self.revoke_credential_record = revoke_credential_record

    async def execute(self, user):
        """本人のService Token発行を依頼・復旧し、Hubトークンは独立して扱う。

        Args:
            user: 操作対象のJupyterHubユーザー。

        Returns:
            発行済みまたは利用停止中の認証情報レコード。
        """
        async with self.queries.credential_lock(user.name):
            # 同名アカウントが作り直された場合は、以前の所有者の秘密値を引き継がない。
            identity = self.queries.credential_identity(user)
            record = self.store.get("credentials", user.name)
            if record and any((record.get(k) != v for k, v in identity.items())):
                await self.revoke_credential_record.execute(user.name, record)
                self.store.delete("credentials", user.name)
                record = None

            # 外部サービスの応答が途切れても続きから再試行できるよう、各工程を保存する。
            record = record or {**identity, "enabled": True, "state": "unissued"}
            self.store.put("credentials", user.name, record)
            if not record["enabled"]:
                return record
            if record["state"] == "revoking_cloudflare":
                raise ValueError("Service Tokenの失効処理が完了していません")
            if self.credential_repository.load_credentials(user.name).available:
                return record
            record["state"] = (
                "issuing" if record["state"] == "unissued" else record["state"]
            )
            record["service_requested"] = True
            self.store.put("credentials", user.name, record)

            if not record.get("cf_token_id"):
                token = await self.cloudflare.issue(
                    f"HPC {self.config.public_host} {user.name} {identity['uid']} {identity['hub_user_id']}"
                )
                record.update(
                    cf_token_id=token["id"],
                    client_id=token["client_id"],
                    client_secret=token["client_secret"],
                )
                self.store.put("credentials", user.name, record)
            if record.get("state") == "rotating_cloudflare":
                token = await self.cloudflare.rotate(record["cf_token_id"])
                record.update(client_secret=token["client_secret"])
                record["state"] = "issuing"
                self.store.put("credentials", user.name, record)

            credentials = self.credential_repository.load_credentials(user.name)
            credentials.complete_issuance()
            record.update(
                state=credentials.state.value,
                enabled=credentials.enabled,
                updated_at=time.time(),
            )
            self.store.put("credentials", user.name, record)
            return record

    async def revoke_service(self, user):
        """本人のService Tokenだけを失効し、Hubトークンと利用許可を維持する。

        Args:
            user: 操作対象のHubユーザー。

        Returns:
            Service Tokenの秘密値を除いた認証情報。
        """
        async with self.queries.credential_lock(user.name):
            record = self.queries.credential_record(user)
            if not record.get("enabled"):
                raise ValueError("自作APIの利用は管理者が停止しています")
            record["state"] = "revoking_cloudflare"
            self.store.put("credentials", user.name, record)
            if record.get("cf_token_id"):
                await self.cloudflare.remove(record["cf_token_id"])
            for key in ("cf_token_id", "client_id", "client_secret"):
                record.pop(key, None)
            record.update(
                state="unissued", service_requested=False, updated_at=time.time()
            )
            self.store.put("credentials", user.name, record)
            return record


class RotateApiCredentialsUseCase:
    def __init__(
        self,
        *,
        queries: ApiQueries,
        credential_repository: ApiCredentialRepository,
        cloudflare: CloudflareAccess,
        store: RecordStore,
        hub_tokens: HubTokens,
        hub_credentials,
        config: ApiConfiguration,
    ):
        """この操作に必要な接続先と処理の依存を保持する。

        Args:
            queries: 所有者を照合したレコード取得とユーザー単位の排他制御。
            credential_repository: 認証情報の所有者と状態を読み書きする保存先。
            cloudflare: Cloudflare Accessのトークンと公開先を管理する接続先。
            store: 認証情報・公開設定・ポート保護のレコード保存先。
            hub_tokens: JupyterHubのユーザー取得と専用トークン管理を行う接続先。
            hub_credentials: Hubトークンの独立した再発行操作。
            config: Service Token発行機能の有効状態。
        """
        self.queries = queries
        self.credential_repository = credential_repository
        self.cloudflare = cloudflare
        self.store = store
        self.hub_tokens = hub_tokens
        self.hub_credentials = hub_credentials
        self.config = config

    async def execute(self, user, kind):
        """選ばれたサービスのトークンだけを再発行し、古い接続情報を無効にする。

        Args:
            user: 操作対象のJupyterHubユーザー。
            kind: 再発行するサービス。cloudflareまたはjupyterhub。

        Returns:
            選択したトークンを更新した認証情報レコード。

        Raises:
            ValueError: 再発行対象が不正、または認証情報が利用できない場合。
        """
        if kind not in {"cloudflare", "jupyterhub"}:
            raise ValueError("再発行対象が不正です")

        if kind == "jupyterhub":
            return await self.hub_credentials.execute(user, "rotate")
        async with self.queries.credential_lock(user.name):
            record = self.queries.credential_record(user)
            credentials = self.credential_repository.load_credentials(user.name)
            credentials.begin_rotation(kind)
            record["state"] = credentials.state.value
            # 再発行中は旧トークンによる呼び出しも拒否する。
            self.credential_repository.save_credentials(credentials)

            token = await self.cloudflare.rotate(record["cf_token_id"])
            record["client_secret"] = token["client_secret"]

            credentials = self.credential_repository.load_credentials(user.name)
            credentials.complete_issuance()
            record.update(
                state=credentials.state.value,
                enabled=credentials.enabled,
                updated_at=time.time(),
            )
            self.store.put("credentials", user.name, record)
            return record


class RevokeCredentialRecordUseCase:
    def __init__(
        self,
        *,
        credential_repository: ApiCredentialRepository,
        hub_tokens: HubTokens,
        cloudflare: CloudflareAccess,
        store: RecordStore,
    ):
        """この操作に必要な接続先と処理の依存を保持する。

        Args:
            credential_repository: 認証情報の所有者と状態を読み書きする保存先。
            hub_tokens: JupyterHubのユーザー取得と専用トークン管理を行う接続先。
            cloudflare: Cloudflare Accessのトークンと公開先を管理する接続先。
            store: 認証情報・公開設定・ポート保護のレコード保存先。
        """
        self.credential_repository = credential_repository
        self.hub_tokens = hub_tokens
        self.cloudflare = cloudflare
        self.store = store

    async def execute(self, username, record):
        """ローカルで利用を拒否し、HubとCloudflareのトークンを失効させる。

        Args:
            username: 対象のLinuxユーザー名。
            record: 対象の認証情報またはAPI公開設定の保存レコード。
        """
        credentials = self.credential_repository.load_credentials(username)
        credentials.begin_revocation()
        record.update(enabled=credentials.enabled, state=credentials.state.value)
        # 外部サービスの失効に失敗しても、ポータルでの利用は先に停止する。
        self.credential_repository.save_credentials(credentials)

        self.hub_tokens.revoke(record.get("hub_token_id"))
        self.hub_tokens.revoke_orphans(username)
        for token_id in record.get("revoke_pending", []):
            self.hub_tokens.revoke(token_id)
        if record.get("cf_token_id"):
            await self.cloudflare.remove(record["cf_token_id"])

        # 両サービスの失効を確認したら、保存済みの秘密値を破棄する。
        credentials.complete_revocation()
        record = {
            "uid": credentials.owner.uid,
            "hub_user_id": credentials.owner.hub_user_id,
            "enabled": credentials.enabled,
            "state": credentials.state.value,
        }
        self.store.put("credentials", username, record)


class RevokeApiCredentialsUseCase:
    def __init__(
        self,
        *,
        queries: ApiQueries,
        store: RecordStore,
        revoke_credential_record: RevokeCredentialRecordUseCase,
    ):
        """この操作に必要な接続先と処理の依存を保持する。

        Args:
            queries: 所有者を照合したレコード取得とユーザー単位の排他制御。
            store: 認証情報・公開設定・ポート保護のレコード保存先。
            revoke_credential_record: 保存された両サービスのトークンを失効させる操作。
        """
        self.queries = queries
        self.store = store
        self.revoke_credential_record = revoke_credential_record

    async def execute(self, username):
        """ユーザー単位で排他し、保存済みの認証情報を失効させる。

        Args:
            username: 対象のLinuxユーザー名。
        """
        async with self.queries.credential_lock(username):
            record = self.store.get("credentials", username)
            if record:
                await self.revoke_credential_record.execute(username, record)


class EnableApiCredentialsUseCase:
    def __init__(
        self,
        *,
        queries: ApiQueries,
        store: RecordStore,
        credential_repository: ApiCredentialRepository,
        hub_credentials,
    ):
        """この操作に必要な接続先と処理の依存を保持する。

        Args:
            queries: 所有者を照合したレコード取得とユーザー単位の排他制御。
            store: 認証情報・公開設定・ポート保護のレコード保存先。
            credential_repository: 認証情報の所有者と状態を読み書きする保存先。
            hub_credentials: Service Tokenから独立したHubトークンの準備操作。
        """
        self.queries = queries
        self.store = store
        self.credential_repository = credential_repository
        self.hub_credentials = hub_credentials

    async def execute(self, user):
        """失効完了後に利用を許可し、Hubトークンだけを自動で準備する。

        Args:
            user: 操作対象のJupyterHubユーザー。

        Returns:
            Hubトークンを準備したService Token未発行のレコード。

        Raises:
            ValueError: 認証情報の失効が完了していない場合。
        """
        async with self.queries.credential_lock(user.name):
            record = self.store.get("credentials", user.name)
            if record:
                self.credential_repository.load_credentials(
                    user.name
                ).require_reenableable()
            self.store.put(
                "credentials",
                user.name,
                {
                    **self.queries.credential_identity(user),
                    "enabled": True,
                    "state": "unissued",
                },
            )

        return await self.hub_credentials.execute(user)


class DisableUserApisUseCase:
    def __init__(
        self,
        *,
        queries: ApiQueries,
        store: RecordStore,
        credential_repository: ApiCredentialRepository,
        hub_tokens: HubTokens,
        revoke_credentials: RevokeApiCredentialsUseCase,
        unpublish_all: UnpublishUserApisUseCase,
    ):
        """この操作に必要な接続先と処理の依存を保持する。

        Args:
            queries: 所有者を照合したレコード取得とユーザー単位の排他制御。
            store: 認証情報・公開設定・ポート保護のレコード保存先。
            credential_repository: 認証情報の所有者と状態を読み書きする保存先。
            hub_tokens: JupyterHubのユーザー取得と専用トークン管理を行う接続先。
            revoke_credentials: ユーザー単位で認証情報を失効させる操作。
            unpublish_all: 対象ユーザーの全APIを公開停止にする操作。
        """
        self.queries = queries
        self.store = store
        self.credential_repository = credential_repository
        self.hub_tokens = hub_tokens
        self.revoke_credentials = revoke_credentials
        self.unpublish_all = unpublish_all

    async def execute(self, username):
        """認証を先に拒否し、全APIの公開停止とトークン失効を進める。

        Args:
            username: 対象のLinuxユーザー名。
        """
        async with self.queries.credential_lock(username):
            record = self.store.get("credentials", username)
            if record is None:
                user = await self.hub_tokens.user(username)
                record = {
                    **self.queries.credential_identity(user),
                    "enabled": True,
                    "state": "unissued",
                }
                self.store.put("credentials", username, record)
            if record:
                credentials = self.credential_repository.load_credentials(username)
                credentials.begin_revocation()
                record.update(
                    enabled=credentials.enabled, state=credentials.state.value
                )
                self.credential_repository.save_credentials(credentials)
                self.hub_tokens.revoke(record.get("hub_token_id"))

        try:
            await self.unpublish_all.execute(username)
        finally:
            # 公開先の削除が失敗してもトークンの失効を試み、定期同期で残りを回収する。
            await self.revoke_credentials.execute(username)


class DeleteUserApiRecordsUseCase:
    def __init__(
        self,
        *,
        queries: ApiQueries,
        store: RecordStore,
        remove_remote_publication: RemoveRemoteApiPublicationUseCase,
    ):
        """この操作に必要な接続先と処理の依存を保持する。

        Args:
            queries: 所有者を照合したレコード取得とユーザー単位の排他制御。
            store: 認証情報・公開設定・ポート保護のレコード保存先。
            remove_remote_publication: Cloudflareの公開先を削除する操作。
        """
        self.queries = queries
        self.store = store
        self.remove_remote_publication = remove_remote_publication

    async def execute(self, username):
        """失効完了を確認し、Cloudflareの公開先とローカル登録を削除する。

        Args:
            username: 対象のLinuxユーザー名。

        Raises:
            ValueError: 認証情報の失効が完了していない場合。
        """
        async with self.queries.credential_lock(username):
            record = self.store.get("credentials", username)
            if record and record.get("state") != "disabled":
                raise ValueError("資格情報の失効が未完了です")

            for key in self.store.names("publications"):
                if key.startswith(username + "/"):
                    await self.remove_remote_publication.execute(
                        self.store.get("publications", key)
                    )
                    self.store.delete("publications", key)

            self.store.delete("credentials", username)
