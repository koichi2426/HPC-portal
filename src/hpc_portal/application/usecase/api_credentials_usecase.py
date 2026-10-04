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
        self.queries = queries
        self.store = store
        self.credential_repository = credential_repository
        self.hub_tokens = hub_tokens
        self.cloudflare = cloudflare
        self.config = config
        self.revoke_credential_record = revoke_credential_record

    async def execute(self, user):
        async with self.queries.credential_lock(user.name):
            identity = self.queries.credential_identity(user)
            record = self.store.get("credentials", user.name)
            if record and any((record.get(k) != v for k, v in identity.items())):
                await self.revoke_credential_record.execute(user.name, record)
                self.store.delete("credentials", user.name)
                record = None
            record = record or {**identity, "enabled": True, "state": "issuing"}
            self.store.put("credentials", user.name, record)
            if not record["enabled"]:
                return record
            if self.credential_repository.load_credentials(
                user.name
            ).available and self.hub_tokens.valid(record, user):
                return record
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
            if not self.hub_tokens.valid(record, user) or record.get(
                "hub_token_id"
            ) in record.get("revoke_pending", []):
                self.hub_tokens.revoke_orphans(
                    user.name, keep_id=record.get("hub_token_id")
                )
                record.update(self.hub_tokens.issue(user))
                self.store.put("credentials", user.name, record)
            for token_id in record.get("revoke_pending", []):
                self.hub_tokens.revoke(token_id)
            record.pop("revoke_pending", None)
            credentials = self.credential_repository.load_credentials(user.name)
            credentials.complete_issuance()
            record.update(
                state=credentials.state.value,
                enabled=credentials.enabled,
                updated_at=time.time(),
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
    ):
        self.queries = queries
        self.credential_repository = credential_repository
        self.cloudflare = cloudflare
        self.store = store
        self.hub_tokens = hub_tokens

    async def execute(self, user, kind):
        if kind not in {"cloudflare", "jupyterhub"}:
            raise ValueError("再発行対象が不正です")
        async with self.queries.credential_lock(user.name):
            record = self.queries.credential_record(user)
            credentials = self.credential_repository.load_credentials(user.name)
            credentials.begin_rotation(kind)
            record["state"] = credentials.state.value
            # 再発行中は旧トークンによる呼び出しも拒否する。
            self.credential_repository.save_credentials(credentials)
            if kind == "cloudflare":
                token = await self.cloudflare.rotate(record["cf_token_id"])
                record["client_secret"] = token["client_secret"]
            else:
                old_id = record["hub_token_id"]
                record["revoke_pending"] = [old_id]
                self.store.put("credentials", user.name, record)
                record.update(self.hub_tokens.issue(user))
                self.store.put("credentials", user.name, record)
                self.hub_tokens.revoke(old_id)
                record.pop("revoke_pending", None)
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
        self.credential_repository = credential_repository
        self.hub_tokens = hub_tokens
        self.cloudflare = cloudflare
        self.store = store

    async def execute(self, username, record):
        credentials = self.credential_repository.load_credentials(username)
        credentials.begin_revocation()
        record.update(enabled=credentials.enabled, state=credentials.state.value)
        self.credential_repository.save_credentials(credentials)
        self.hub_tokens.revoke(record.get("hub_token_id"))
        self.hub_tokens.revoke_orphans(username)
        for token_id in record.get("revoke_pending", []):
            self.hub_tokens.revoke(token_id)
        if record.get("cf_token_id"):
            await self.cloudflare.remove(record["cf_token_id"])
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
        self.queries = queries
        self.store = store
        self.revoke_credential_record = revoke_credential_record

    async def execute(self, username):
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
        issue_credentials: IssueApiCredentialsUseCase,
    ):
        self.queries = queries
        self.store = store
        self.credential_repository = credential_repository
        self.issue_credentials = issue_credentials

    async def execute(self, user):
        async with self.queries.credential_lock(user.name):
            record = self.store.get("credentials", user.name)
            if record:
                self.credential_repository.load_credentials(
                    user.name
                ).require_reenableable()
            self.store.delete("credentials", user.name)
        return await self.issue_credentials.execute(user)


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
        self.queries = queries
        self.store = store
        self.credential_repository = credential_repository
        self.hub_tokens = hub_tokens
        self.revoke_credentials = revoke_credentials
        self.unpublish_all = unpublish_all

    async def execute(self, username):
        async with self.queries.credential_lock(username):
            record = self.store.get("credentials", username)
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
            await self.revoke_credentials.execute(username)


class DeleteUserApiRecordsUseCase:
    def __init__(
        self,
        *,
        queries: ApiQueries,
        store: RecordStore,
        remove_remote_publication: RemoveRemoteApiPublicationUseCase,
    ):
        self.queries = queries
        self.store = store
        self.remove_remote_publication = remove_remote_publication

    async def execute(self, username):
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
