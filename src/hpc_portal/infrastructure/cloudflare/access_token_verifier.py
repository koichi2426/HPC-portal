"""Cloudflare assertions are signature-verified against a fixed team issuer."""

import asyncio
import secrets
import time

import aiohttp
import jwt


class AccessVerifier:
    def __init__(self, config):
        self.config = config
        self.cache = {}
        self.refresh_at = 0
        self.expires_at = 0
        self.lock = asyncio.Lock()

    async def key(self, kid):
        if not isinstance(kid, str) or len(kid) > 128:
            raise ValueError("Access assertion is invalid")
        async with self.lock:
            now = time.monotonic()
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
        if not isinstance(assertion, str) or not assertion or len(assertion) > 8192:
            raise ValueError("Access assertion is missing")
        try:
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
