"""外部からのAPI呼び出しを所有者・資格情報・公開状態で認可する。"""

from __future__ import annotations

import secrets

from hpc_portal.application.ports.api_query_ports import ApiQueries
from hpc_portal.application.ports.external_api_ports import (
    AccessVerifier,
    ApiConfiguration,
    PortGuard,
)
from hpc_portal.domain.errors import UseCaseError
from hpc_portal.domain.external_api.repositories import (
    ApiCredentialRepository,
    ApiPublicationRepository,
)


class AuthorizeApiInvocationUseCase:
    def __init__(
        self,
        *,
        queries: ApiQueries,
        credential_repository: ApiCredentialRepository,
        publication_repository: ApiPublicationRepository,
        access_verifier: AccessVerifier,
        config: ApiConfiguration,
        port_guard: PortGuard,
    ):
        """この操作に必要な接続先と処理の依存を保持する。

        Args:
            queries: 所有者を照合したレコード取得とユーザー単位の排他制御。
            credential_repository: 認証情報の所有者と状態を読み書きする保存先。
            publication_repository: API公開設定の所有者と状態を読み書きする保存先。
            access_verifier: Cloudflare Access JWTの署名と所有者を検証する接続先。
            config: 外部APIの公開ドメイン・接続制限・保存先などの設定。
            port_guard: 公開先ポートへの直接接続を制限・確認する接続先。
        """
        self.queries = queries
        self.credential_repository = credential_repository
        self.publication_repository = publication_repository
        self.access_verifier = access_verifier
        self.config = config
        self.port_guard = port_guard

    async def execute(self, user, token, username, name, headers, body_size):
        """本人の両トークン・Access JWT・公開状態を確認し、転送可能な登録を返す。

        Args:
            user: 操作対象のJupyterHubユーザー。
            token: 認可に使用するJupyterHubのトークンレコード。
            username: 対象のLinuxユーザー名。
            name: 呼び出す登録APIの識別名。
            headers: 転送元または転送先のHTTPヘッダー。
            body_size: リクエスト本文のサイズ。単位はバイト。

        Returns:
            所有者と認証を確認した、転送可能なAPI公開レコード。

        Raises:
            UseCaseError: 本人認証・登録状態・本文サイズの条件を満たさない場合。codeで失敗種別を区別する。
            ValueError: 公開先ポートの保護状態を確認できない場合。
        """
        if not token or not token.user or (not user) or (user.name != username):
            raise UseCaseError("APIへのアクセスが許可されていません", "forbidden")

        try:
            record = self.queries.credential_record(user)
            credentials = self.credential_repository.load_credentials(username)
        except (ValueError, KeyError):
            raise UseCaseError(
                "APIへのアクセスが許可されていません", "forbidden"
            ) from None
        # 通常のHubトークンではなく、本人のAPI専用に発行したトークンへ限定する。
        expected_scope = f"custom:external-api:invoke!user={username}"
        if (
            not credentials
            or not credentials.available
            or token.id != record.get("hub_token_id")
            or expected_scope not in token.scopes
        ):
            raise UseCaseError("APIへのアクセスが許可されていません", "forbidden")

        # 再発行前の認証情報で作られたAccess JWTも利用できないようにする。
        for header, key in (
            ("CF-Access-Client-ID", "client_id"),
            ("CF-Access-Client-Secret", "client_secret"),
        ):
            if not secrets.compare_digest(
                headers.get(header, "").encode(), record[key].encode()
            ):
                raise UseCaseError("APIへのアクセスが許可されていません", "forbidden")

        try:
            app = self.queries.get_publication(user, name)
            publication = self.publication_repository.load_publication(username, name)
        except (ValueError, KeyError):
            raise UseCaseError("APIの登録が見つかりません", "missing") from None
        if not publication or not publication.available:
            raise UseCaseError("APIは公開されていません", "unavailable")

        try:
            await self.access_verifier.verify(
                headers.get("Cf-Access-Jwt-Assertion", ""), app, record
            )
        except ValueError:
            raise UseCaseError(
                "APIへのアクセスが許可されていません", "forbidden"
            ) from None

        if body_size > self.config.body_limit:
            raise UseCaseError("リクエスト本文が大きすぎます", "too_large")

        await self.port_guard.check(app["target"])
        return app
