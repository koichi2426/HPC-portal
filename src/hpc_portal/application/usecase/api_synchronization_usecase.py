"""認証情報とAPI公開設定の初期同期・中断時の復旧。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from hpc_portal.application.ports.external_api_ports import (
    HubTokens,
    PortGuard,
    RecordStore,
)

if TYPE_CHECKING:
    from hpc_portal.application.usecase.api_credentials_usecase import (
        DisableUserApisUseCase,
        IssueApiCredentialsUseCase,
    )
    from hpc_portal.application.usecase.api_publication_usecase import (
        RefreshApiPublicationUseCase,
    )

log = logging.getLogger("jupyterhub.external-api")


class SynchronizeUserApisUseCase:
    def __init__(
        self,
        *,
        sync_lock: asyncio.Lock,
        users_snapshot: Callable[[], list[dict]],
        store: RecordStore,
        hub_tokens: HubTokens,
        port_guard: PortGuard,
        disable_user: DisableUserApisUseCase,
        issue_credentials: IssueApiCredentialsUseCase,
        refresh_publication: RefreshApiPublicationUseCase,
    ):
        self.sync_lock = sync_lock
        self.users_snapshot = users_snapshot
        self.store = store
        self.hub_tokens = hub_tokens
        self.port_guard = port_guard
        self.disable_user = disable_user
        self.issue_credentials = issue_credentials
        self.refresh_publication = refresh_publication

    async def execute(self):
        """重複実行を避け、削除ユーザーの失効・未完了の発行・公開状態を順に同期する。"""
        if self.sync_lock.locked():
            return
        async with self.sync_lock:
            rows = await asyncio.to_thread(self.users_snapshot)
            usernames = {row["username"] for row in rows}
            for name in self.store.names("credentials"):
                record = self.store.get("credentials", name)
                if name not in usernames or record.get("state") == "revoking":
                    try:
                        await self.disable_user.execute(name)
                    except Exception:
                        log.warning("External API revocation pending for %s", name)

            for row in rows:
                try:
                    await self.issue_credentials.execute(
                        await self.hub_tokens.user(row["username"])
                    )
                except Exception:
                    log.warning("External API issuance pending for %s", row["username"])

            for key in self.store.names("publications"):
                try:
                    await self.refresh_publication.execute(
                        self.store.get("publications", key)
                    )
                except Exception:
                    log.warning("External API publication recovery pending for %s", key)

            await self.port_guard.reconcile()
