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
        self.queries = queries
        self.credential_repository = credential_repository
        self.publication_repository = publication_repository
        self.access_verifier = access_verifier
        self.config = config
        self.port_guard = port_guard

    async def execute(self, user, token, username, name, headers, body_size):
        """本人の両トークン・Access JWT・公開状態を確認し、転送可能な登録を返す。"""
        if not token or not token.user or (not user) or (user.name != username):
            raise UseCaseError("APIへのアクセスが許可されていません", "forbidden")
        try:
            record = self.queries.credential_record(user)
            credentials = self.credential_repository.load_credentials(username)
        except (ValueError, KeyError):
            raise UseCaseError(
                "APIへのアクセスが許可されていません", "forbidden"
            ) from None
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
