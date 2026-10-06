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
        """この操作に必要な接続先と処理の依存を保持する。

        Args:
            queries: 所有者を照合したレコード取得とユーザー単位の排他制御。
            store: 認証情報・公開設定・ポート保護のレコード保存先。
            publication_repository: API公開設定の所有者と状態を読み書きする保存先。
            accounts: Linuxユーザーの照合・作成・変更を行う接続先。
            config: 外部APIの公開ドメイン・接続制限・保存先などの設定。
            credential_repository: 認証情報の所有者と状態を読み書きする保存先。
            listeners: 待受候補の取得とプロセスの同一性確認を行う接続先。
            publish_registration: 保護・動作確認後にCloudflareの公開先を設定する操作。
        """
        self.queries = queries
        self.store = store
        self.publication_repository = publication_repository
        self.accounts = accounts
        self.config = config
        self.credential_repository = credential_repository
        self.listeners = listeners
        self.publish_registration = publish_registration

    async def execute(self, user, settings):
        """所有者・認証状態・待受候補を確認し、登録を保存して公開設定へ進む。

        Args:
            user: 操作対象のJupyterHubユーザー。
            settings: 検証済みのAPI名・待受候補・表示名・動作確認パス。

        Returns:
            接続先とCloudflare設定を保存したAPI公開レコード。

        Raises:
            ValueError: 所有者・登録数・接続先を確認できない、または認証情報を利用できない場合。
        """
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

            # 登録する接続先はポート番号だけでなく、本人のプロセスとソケットまで確定する。
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
        """この操作に必要な接続先と処理の依存を保持する。

        Args:
            queries: 所有者を照合したレコード取得とユーザー単位の排他制御。
            publication_repository: API公開設定の所有者と状態を読み書きする保存先。
            store: 認証情報・公開設定・ポート保護のレコード保存先。
            port_guard: 公開先ポートへの直接接続を制限・確認する接続先。
            health: 公開先APIの保護状態とHTTP応答を確認する処理。
            config: 外部APIの公開ドメイン・接続制限・保存先などの設定。
            cloudflare: Cloudflare Accessのトークンと公開先を管理する接続先。
            listeners: 待受候補の取得とプロセスの同一性確認を行う接続先。
        """
        self.queries = queries
        self.publication_repository = publication_repository
        self.store = store
        self.port_guard = port_guard
        self.health = health
        self.config = config
        self.cloudflare = cloudflare
        self.listeners = listeners

    async def execute(self, record, credential):
        """ポート保護と接続確認後、本人のService Tokenだけを許可する公開先を設定する。

        Args:
            record: 接続先と希望状態を含むAPI公開の保存レコード。
            credential: 公開先に紐付ける本人の認証情報レコード。

        Returns:
            公開を完了したAPI公開レコード。
        """
        key = self.queries.publication_key(record["username"], record["name"])
        publication = self.publication_repository.load_publication(
            record["username"], record["name"]
        )
        publication.begin_publication()
        record.update(state=publication.state.value, desired=publication.desired.value)
        record["remote_clean"] = False
        self.store.put("publications", key, record)
        try:
            # Accessを作る前に、ポートへの直接接続を制限して動作を確認する。
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
            # 途中で失敗した状態も保存し、定期同期から再試行できるようにする。
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
        """この操作に必要な接続先と処理の依存を保持する。

        Args:
            queries: 所有者を照合したレコード取得とユーザー単位の排他制御。
            publication_repository: API公開設定の所有者と状態を読み書きする保存先。
            credential_repository: 認証情報の所有者と状態を読み書きする保存先。
            store: 認証情報・公開設定・ポート保護のレコード保存先。
            publish_registration: 保護・動作確認後にCloudflareの公開先を設定する操作。
            remove_remote_publication: Cloudflareの公開先を削除する操作。
        """
        self.queries = queries
        self.publication_repository = publication_repository
        self.credential_repository = credential_repository
        self.store = store
        self.publish_registration = publish_registration
        self.remove_remote_publication = remove_remote_publication

    async def execute(self, user, name, action):
        """本人の登録を確認し、再公開・公開停止・削除の希望を反映する。

        Args:
            user: 操作対象のJupyterHubユーザー。
            name: ユーザーが登録したAPIの識別名。
            action: 実行する操作の識別名。

        Returns:
            操作を反映したAPI公開レコード。

        Raises:
            ValueError: 操作が不正、対象を取得できない、または再公開時に認証情報を利用できない場合。
        """
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
        """この操作に必要な接続先と処理の依存を保持する。

        Args:
            cloudflare: Cloudflare Accessのトークンと公開先を管理する接続先。
            config: 外部APIの公開ドメイン・接続制限・保存先などの設定。
            store: 認証情報・公開設定・ポート保護のレコード保存先。
            queries: 所有者を照合したレコード取得とユーザー単位の排他制御。
        """
        self.cloudflare = cloudflare
        self.config = config
        self.store = store
        self.queries = queries

    async def execute(self, record):
        """Cloudflareの公開先を削除し、同期処理が再試行できる形で結果を保存する。

        Args:
            record: 接続先と希望状態を含むAPI公開の保存レコード。
        """
        if record.get("remote_clean") and (not record.get("cf_app_id")):
            return

        await self.cloudflare.remove_app(
            f"HPC API / {self.config.public_host} / {record['username']} / {record['name']}",
            record.get("cf_app_id"),
        )

        # 削除の応答が得られるまではIDを残し、同じ管理対象への再試行を可能にする。
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
        """この操作に必要な接続先と処理の依存を保持する。

        Args:
            queries: 所有者を照合したレコード取得とユーザー単位の排他制御。
            store: 認証情報・公開設定・ポート保護のレコード保存先。
            publication_repository: API公開設定の所有者と状態を読み書きする保存先。
            credential_repository: 認証情報の所有者と状態を読み書きする保存先。
            listeners: 待受候補の取得とプロセスの同一性確認を行う接続先。
            health: 公開先APIの保護状態とHTTP応答を確認する処理。
            publish_registration: 保護・動作確認後にCloudflareの公開先を設定する操作。
            remove_remote_publication: Cloudflareの公開先を削除する操作。
        """
        self.queries = queries
        self.store = store
        self.publication_repository = publication_repository
        self.credential_repository = credential_repository
        self.listeners = listeners
        self.health = health
        self.publish_registration = publish_registration
        self.remove_remote_publication = remove_remote_publication

    async def execute(self, record):
        """希望状態と現在の認証・待受状態を照合し、公開設定や削除を再試行する。

        Args:
            record: 接続先と希望状態を含むAPI公開の保存レコード。

        Returns:
            再設定した場合は公開レコード。再設定しない場合はNone。
        """
        async with self.queries.publication_lock(record["username"]):
            # ロック待ちの間に利用者が変更・削除した可能性があるため、保存値を読み直す。
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
                    # 同じポートを別プロセスが使っていても、自動で公開先を付け替えない。
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
        """この操作に必要な接続先と処理の依存を保持する。

        Args:
            queries: 所有者を照合したレコード取得とユーザー単位の排他制御。
            store: 認証情報・公開設定・ポート保護のレコード保存先。
            publication_repository: API公開設定の所有者と状態を読み書きする保存先。
            remove_remote_publication: Cloudflareの公開先を削除する操作。
        """
        self.queries = queries
        self.store = store
        self.publication_repository = publication_repository
        self.remove_remote_publication = remove_remote_publication

    async def execute(self, username):
        """全登録を先に公開停止へ変更し、その後Cloudflareの公開先を削除する。

        Args:
            username: 対象のLinuxユーザー名。
        """
        async with self.queries.publication_lock(username):
            records = [
                self.store.get("publications", k)
                for k in self.store.names("publications")
                if k.startswith(username + "/")
            ]

            # 外部サービスの削除に着手する前に、このユーザーの全APIへの転送を止める。
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
        """この操作に必要な接続先と処理の依存を保持する。

        Args:
            accounts: Linuxユーザーの照合・作成・変更を行う接続先。
            listeners: 待受候補の取得とプロセスの同一性確認を行う接続先。
        """
        self.accounts = accounts
        self.listeners = listeners

    async def execute(self, user):
        """本人の待受プロセスと、その時点で利用できる空きポート候補を取得する。

        Args:
            user: 操作対象のJupyterHubユーザー。

        Returns:
            確認時刻・空きポート候補・本人の待受プロセス一覧。
        """
        entry = self.accounts.getpwnam(user.name)
        return await asyncio.to_thread(self.listeners.ports, entry.pw_uid)
