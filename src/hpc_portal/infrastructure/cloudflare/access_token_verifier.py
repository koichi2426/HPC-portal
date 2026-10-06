"""固定したチームURLの署名鍵で、Cloudflare Access JWTを検証する。"""

import asyncio
import secrets
import time

import aiohttp
import jwt


class AccessVerifier:
    def __init__(self, config):
        """固定のAccess発行元と、公開鍵のキャッシュ・更新ロックを準備する。

        Args:
            config: Cloudflare Accessの発行元を固定する設定。
        """
        self.config = config
        self.cache = {}
        self.refresh_at = 0
        self.expires_at = 0
        self.lock = asyncio.Lock()

    async def key(self, kid):
        """固定したチームURLからRSA署名鍵を取得し、有効期限内は共有する。

        Args:
            kid: JWTヘッダーが指定する署名鍵の識別子。

        Returns:
            指定されたkidに対応する署名検証用の公開鍵。

        Raises:
            ValueError: kidが不正、または有効な署名鍵を取得・特定できない場合。
        """
        if not isinstance(kid, str) or len(kid) > 128:
            raise ValueError("Access assertion is invalid")

        async with self.lock:
            now = time.monotonic()
            # 未知のkidで更新が連発しないよう、短い再取得間隔とキャッシュ期限を分ける。
            if now >= self.expires_at or (
                kid not in self.cache and now >= self.refresh_at
            ):
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as session:
                    async with session.get(
                        self.config.access_issuer + "/cdn-cgi/access/certs",
                        allow_redirects=False,
                    ) as response:
                        if response.status != 200:
                            raise ValueError("Access signing keys are unavailable")

                        raw = bytearray()
                        async for chunk in response.content.iter_chunked(8192):
                            raw.extend(chunk)
                            if len(raw) > 65536:
                                raise ValueError("Access signing keys are invalid")
                        import json

                        data = json.loads(raw)

                self.cache = {
                    item["kid"]: jwt.PyJWK.from_dict(item).key
                    for item in data["keys"]
                    if item.get("kty") == "RSA"
                }
                self.refresh_at, self.expires_at = now + 15, now + 300

            if kid not in self.cache:
                raise ValueError("Access signing key is unknown")
            return self.cache[kid]

    async def verify(self, assertion, app, credentials):
        """JWTの署名・期限・公開先・Service Tokenの所有者を照合する。

        Args:
            assertion: Cloudflare Accessが発行したJWT。
            app: 検証対象のAccessアプリのaudを含むAPI公開レコード。
            credentials: 本人のService TokenのClient IDを含む認証情報レコード。

        Raises:
            ValueError: JWTの署名・期限・発行元・aud・トークン所有者を確認できない場合。
        """
        if not isinstance(assertion, str) or not assertion or len(assertion) > 8192:
            raise ValueError("Access assertion is missing")

        try:
            # 未検証ヘッダーは鍵の選択だけに使い、発行元・用途・署名方式は固定して検証する。
            key = await self.key(jwt.get_unverified_header(assertion).get("kid"))
            claims = jwt.decode(
                assertion,
                key,
                algorithms=["RS256"],
                audience=app["cf_aud"],
                issuer=self.config.access_issuer,
                options={"require": ["exp", "iat", "iss", "aud", "common_name"]},
            )

            if claims.get("type") != "app" or not secrets.compare_digest(
                str(claims["common_name"]).encode(), credentials["client_id"].encode()
            ):
                raise ValueError("Access identity mismatch")
        except (jwt.PyJWTError, KeyError, TypeError):
            raise ValueError("Access assertion is invalid") from None
