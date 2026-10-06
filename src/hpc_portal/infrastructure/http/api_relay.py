"""接続先のプロセスを再確認し、認証ヘッダーを除いてHTTPを転送する。"""

import asyncio
from contextlib import asynccontextmanager

import aiohttp

HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "host",
}
PRIVATE_HEADERS = {"authorization", "cookie", "set-cookie", "x-hpc-internal-token"}


def clean_headers(headers, *, response=False):
    """接続単位のヘッダーと認証情報を除き、転送する情報を限定する。"""
    nominated = set()
    for key, value in headers.items():
        if key.lower() == "connection":
            nominated.update(v.strip().lower() for v in value.split(","))
    blocked = HOP_HEADERS | PRIVATE_HEADERS | nominated
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in blocked
        and not key.lower().startswith(("cf-access-", "x-forwarded-", "forwarded"))
    }


class VerifiedConnector(aiohttp.TCPConnector):
    def __init__(self, inventory, target):
        super().__init__(force_close=True)
        self.inventory, self.target = inventory, target

    async def _create_connection(self, req, traces, timeout):
        await asyncio.to_thread(self.inventory.validate, self.target)
        protocol = await super()._create_connection(req, traces, timeout)
        try:
            # 接続中のプロセス交代も検出し、別アプリへ本文やヘッダーを送らない。
            await asyncio.to_thread(self.inventory.validate, self.target)
        except Exception:
            protocol.close()
            raise aiohttp.ClientConnectionError("API listener changed") from None
        return protocol


@asynccontextmanager
async def request(
    inventory, target, method, path, headers=None, body=None, *, timeout=60
):
    if not path.startswith("/") or any(c in path for c in "\r\n\x00"):
        raise ValueError("API path is invalid")
    connector = VerifiedConnector(inventory, target)
    async with aiohttp.ClientSession(
        connector=connector,
        timeout=aiohttp.ClientTimeout(total=timeout),
        auto_decompress=False,
        trust_env=False,
    ) as session:
        async with session.request(
            method,
            f"http://127.0.0.1:{target['port']}" + path,
            headers=clean_headers(headers or {}),
            data=body,
            allow_redirects=False,
        ) as response:
            yield response
