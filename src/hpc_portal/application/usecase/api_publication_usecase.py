"""起動済みAPIの登録・公開・公開停止と接続確認の操作手順。"""

from __future__ import annotations

import asyncio
import time
from urllib.parse import quote

from hpc_portal.application.ports.api_query_ports import ApiQueries
from hpc_portal.application.ports.external_api_ports import (
    ApiConfiguration,
    ApiHealth,
    CloudflareAccess,
    ListenerInventory,
    PortGuard,
    RecordStore,
)
from hpc_portal.application.ports.user_management_ports import UserAccountGateway
from hpc_portal.domain.external_api.publication import ApiPublication, ApiTarget
from hpc_portal.domain.external_api.repositories import (
    ApiCredentialRepository,
    ApiPublicationRepository,
)


class RegisterApiPublicationUseCase:
    def __init__(
        self,
        *,
        queries: ApiQueries,
        store: RecordStore,
        publication_repository: ApiPublicationRepository,
        accounts: UserAccountGateway,
        config: ApiConfiguration,
        credential_repository: ApiCredentialRepository,
        listeners: ListenerInventory,
        publish_registration: ConfigureApiPublicationUseCase,
    ):
        self.queries = queries
        self.store = store
        self.publication_repository = publication_repository
        self.accounts = accounts
        self.config = config
        self.credential_repository = credential_repository
        self.listeners = listeners
        self.publish_registration = publish_registration

    async def execute(self, user, settings):
        """所有者・認証状態・待受候補を確認し、登録を保存して公開設定へ進む。"""
        async with self.queries.publication_lock(user.name):
            key = self.queries.publication_key(user.name, settings.name)
            old = self.store.get("publications", key)
            previous = self.publication_repository.load_publication(
                user.name, settings.name
            )
            if previous and not previous.owner.matches(
                self.accounts.getpwnam(user.name).pw_uid, user.id
            ):
                raise ValueError(
                    "以前のアカウントの登録が残っています。管理者に確認してください"
                )
            if (
                not old
                and len(self.queries.list_publications(user)) >= self.config.max_apps
            ):
                raise ValueError("API の登録数上限に達しています")
            credential = self.queries.credential_record(user)
            credentials = self.credential_repository.load_credentials(user.name)
            credentials.require_available()
            target = await asyncio.to_thread(
                self.listeners.choose,
                credential["uid"],
                settings.candidate,
                settings.port,
            )
            record = {
                "name": settings.name,
                "health_path": settings.health_path,
                "username": user.name,
                "uid": credential["uid"],
                "hub_user_id": user.id,
                "target": target,
                "display_name": settings.display_name or target["display_name"],
                "state": "checking",
                "desired": "published",
                "remote_clean": False,
                "created_at": old["created_at"] if old else time.time(),
            }
            if old:
                record.update({k: old[k] for k in ("cf_app_id", "cf_aud") if k in old})

            publication = ApiPublication(
                credentials.owner,
                settings.name,
                ApiTarget(target.get("uid", credentials.owner.uid), target["port"]),
            )
            publication.begin_publication()
            record.update(
                state=publication.state.value, desired=publication.desired.value
            )
            # 接続先を変える前に、既存URLからの呼び出しを拒否する。
            self.store.put("publications", key, record)
            return await self.publish_registration.execute(record, credential)


class ConfigureApiPublicationUseCase:
    def __init__(
        self,
        *,
        queries: ApiQueries,
        publication_repository: ApiPublicationRepository,
        store: RecordStore,
        port_guard: PortGuard,
        health: ApiHealth,
        config: ApiConfiguration,
        cloudflare: CloudflareAccess,
        listeners: ListenerInventory,
    ):
        self.queries = queries
        self.publication_repository = publication_repository
        self.store = store
        self.port_guard = port_guard
        self.health = health
        self.config = config
        self.cloudflare = cloudflare
        self.listeners = listeners

    async def execute(self, record, credential):
        """ポート保護と接続確認後、本人のService Tokenだけを許可する公開先を設定する。"""
        key = self.queries.publication_key(record["username"], record["name"])
        publication = self.publication_repository.load_publication(
            record["username"], record["name"]
        )
        publication.begin_publication()
        record.update(state=publication.state.value, desired=publication.desired.value)
        record["remote_clean"] = False
        self.store.put("publications", key, record)
        try:
            await self.port_guard.protect(record["target"])
            await self.health.check(record)
            publication.begin_configuration()
            record["state"] = publication.state.value
            self.publication_repository.save_publication(publication)
            base = f"{self.config.public_host}/hub/user-api/{quote(record['username'], safe='')}/{record['name']}"
            app = await self.cloudflare.app(
                f"HPC API / {self.config.public_host} / {record['username']} / {record['name']}",
                [base, base + "/*"],
                [credential["cf_token_id"]],
                record.get("cf_app_id"),
            )
            record.update(
                cf_app_id=app["id"], cf_aud=app["aud"], cf_checked_at=time.time()
            )
            # Cloudflareの更新中にプロセスが終了・交代していないかを再確認する。
            await asyncio.to_thread(self.listeners.validate, record["target"])
            publication.complete_publication()
            record["state"] = publication.state.value
        except Exception:
            publication.fail()
            record["state"] = publication.state.value
            raise
        finally:
            record["checked_at"] = time.time()
            self.store.put("publications", key, record)
        return record


class ChangeApiPublicationUseCase:
    def __init__(
        self,
        *,
        queries: ApiQueries,
        publication_repository: ApiPublicationRepository,
        credential_repository: ApiCredentialRepository,
        store: RecordStore,
        publish_registration: ConfigureApiPublicationUseCase,
        remove_remote_publication: RemoveRemoteApiPublicationUseCase,
    ):
        self.queries = queries
        self.publication_repository = publication_repository
        self.credential_repository = credential_repository
        self.store = store
        self.publish_registration = publish_registration
        self.remove_remote_publication = remove_remote_publication

    async def execute(self, user, name, action):
        """本人の登録を確認し、再公開・公開停止・削除の希望を反映する。"""
        async with self.queries.publication_lock(user.name):
            record = self.queries.get_publication(user, name)
            publication = self.publication_repository.load_publication(user.name, name)
            key = self.queries.publication_key(user.name, name)
            if action == "publish":
                credential = self.queries.credential_record(user)
                self.credential_repository.load_credentials(
                    user.name
                ).require_available()
                publication.begin_publication()
                record.update(
                    state=publication.state.value, desired=publication.desired.value
                )
                return await self.publish_registration.execute(record, credential)
            if action not in {"unpublish", "delete"}:
                raise ValueError("操作が不正です")
            publication.stop(delete=action == "delete")
            record.update(
                state=publication.state.value, desired=publication.desired.value
            )
            # 外部サービスが停止していても、ポータル側の拒否を先に確定する。
            self.publication_repository.save_publication(publication)
            await self.remove_remote_publication.execute(record)
            if action == "delete":
                self.store.delete("publications", key)
            return record


class RemoveRemoteApiPublicationUseCase:
    def __init__(
        self,
        *,
        cloudflare: CloudflareAccess,
        config: ApiConfiguration,
        store: RecordStore,
        queries: ApiQueries,
    ):
        self.cloudflare = cloudflare
        self.config = config
        self.store = store
        self.queries = queries

    async def execute(self, record):
        """Cloudflareの公開先を削除し、同期処理が再試行できる形で結果を保存する。"""
        if record.get("remote_clean") and (not record.get("cf_app_id")):
            return
        await self.cloudflare.remove_app(
            f"HPC API / {self.config.public_host} / {record['username']} / {record['name']}",
            record.get("cf_app_id"),
        )
        record.pop("cf_app_id", None)
        record.pop("cf_aud", None)
        record["remote_clean"] = True
        self.store.put(
            "publications",
            self.queries.publication_key(record["username"], record["name"]),
            record,
        )


class RefreshApiPublicationUseCase:
    def __init__(
        self,
        *,
        queries: ApiQueries,
        store: RecordStore,
        publication_repository: ApiPublicationRepository,
        credential_repository: ApiCredentialRepository,
        listeners: ListenerInventory,
        health: ApiHealth,
        publish_registration: ConfigureApiPublicationUseCase,
        remove_remote_publication: RemoveRemoteApiPublicationUseCase,
    ):
        self.queries = queries
        self.store = store
        self.publication_repository = publication_repository
        self.credential_repository = credential_repository
        self.listeners = listeners
        self.health = health
        self.publish_registration = publish_registration
        self.remove_remote_publication = remove_remote_publication

    async def execute(self, record):
        """希望状態と現在の認証・待受状態を照合し、公開設定や削除を再試行する。"""
        async with self.queries.publication_lock(record["username"]):
            key = self.queries.publication_key(record["username"], record["name"])
            record = self.store.get("publications", key)
            if not record:
                return
            publication = self.publication_repository.load_publication(
                record["username"], record["name"]
            )
            if record["desired"] != "published":
                await self.remove_remote_publication.execute(record)
                if record["desired"] == "deleted":
                    self.store.delete("publications", key)
                return

            credential = self.store.get("credentials", record["username"])
            credentials = self.credential_repository.load_credentials(
                record["username"]
            )
            if not credentials or not credentials.available:
                publication.disable()
            elif publication.owner != credentials.owner:
                publication.stop(delete=True)
            else:
                try:
                    await asyncio.to_thread(self.listeners.validate, record["target"])
                except ValueError:
                    publication.disconnect()
                else:
                    if (
                        publication.needs_configuration
                        or time.time() - record.get("cf_checked_at", 0) >= 300
                    ):
                        return await self.publish_registration.execute(
                            record, credential
                        )
                    try:
                        await self.health.check(record)
                        publication.complete_publication()
                    except Exception:
                        publication.disconnect()

            record.update(
                state=publication.state.value, desired=publication.desired.value
            )
            record["checked_at"] = time.time()
            self.store.put("publications", key, record)


class UnpublishUserApisUseCase:
    def __init__(
        self,
        *,
        queries: ApiQueries,
        store: RecordStore,
        publication_repository: ApiPublicationRepository,
        remove_remote_publication: RemoveRemoteApiPublicationUseCase,
    ):
        self.queries = queries
        self.store = store
        self.publication_repository = publication_repository
        self.remove_remote_publication = remove_remote_publication

    async def execute(self, username):
        """全登録を先に公開停止へ変更し、その後Cloudflareの公開先を削除する。"""
        async with self.queries.publication_lock(username):
            records = [
                self.store.get("publications", k)
                for k in self.store.names("publications")
                if k.startswith(username + "/")
            ]
            for record in records:
                publication = self.publication_repository.load_publication(
                    username, record["name"]
                )
                publication.stop()
                record.update(
                    state=publication.state.value, desired=publication.desired.value
                )
                self.store.put(
                    "publications",
                    self.queries.publication_key(username, record["name"]),
                    record,
                )

            for record in records:
                await self.remove_remote_publication.execute(record)


class ListApiPortsUseCase:
    def __init__(
        self,
        *,
        accounts: UserAccountGateway,
        listeners: ListenerInventory,
    ):
        self.accounts = accounts
        self.listeners = listeners

    async def execute(self, user):
        """本人の待受プロセスと、その時点で利用できる空きポート候補を取得する。"""
        entry = self.accounts.getpwnam(user.name)
        return await asyncio.to_thread(self.listeners.ports, entry.pw_uid)
