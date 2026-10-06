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
        """この操作に必要な接続先と処理の依存を保持する。

        Args:
            sync_lock: 同期処理の重複実行を防ぐ共有ロック。
            users_snapshot: 現在のLinuxユーザー一覧を返す関数。
            store: 認証情報・公開設定・ポート保護のレコード保存先。
            hub_tokens: JupyterHubのユーザー取得と専用トークン管理を行う接続先。
            port_guard: 公開先ポートへの直接接続を制限・確認する接続先。
            disable_user: 対象ユーザーの公開停止と認証情報の失効を行う操作。
            issue_credentials: 両サービスのトークンを揃える操作。
            refresh_publication: 公開先の希望状態と実際の状態を同期する操作。
        """
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

            # 削除済みアカウントと失効途中の認証情報を先に処理する。
            for name in self.store.names("credentials"):
                record = self.store.get("credentials", name)
                if name not in usernames or record.get("state") == "revoking":
                    try:
                        await self.disable_user.execute(name)
                    except Exception:
                        log.warning("External API revocation pending for %s", name)

            # 個別の失敗は次回へ持ち越し、他ユーザーの同期は続ける。
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
