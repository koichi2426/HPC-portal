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
    """接続単位のヘッダーと認証情報を除き、転送する情報を限定する。

    Args:
        headers: 転送元または転送先のHTTPヘッダー。
        response: 応答ヘッダーを整形する呼び出しかを示す互換用の引数。

    Returns:
        転送禁止のヘッダーと認証情報を除いたヘッダー辞書。
    """
    nominated = set()
    for key, value in headers.items():
        if key.lower() == "connection":
            nominated.update(v.strip().lower() for v in value.split(","))

    # Connectionが指定した追加ヘッダーも除き、認証情報をユーザーのAPIへ渡さない。
    blocked = HOP_HEADERS | PRIVATE_HEADERS | nominated
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in blocked
        and not key.lower().startswith(("cf-access-", "x-forwarded-", "forwarded"))
    }


class VerifiedConnector(aiohttp.TCPConnector):
    def __init__(self, inventory, target):
        """接続の前後で検証する待受情報を保持する。

        Args:
            inventory: 公開対象の待受プロセスが登録時と同一か確認する接続先。
            target: 登録時の所有者・プロセス・ソケットを含む接続先情報。
        """
        super().__init__(force_close=True)
        self.inventory, self.target = inventory, target

    async def _create_connection(self, req, traces, timeout):
        """接続前後で待受プロセスを照合して、aiohttpの接続を作成する。

        Args:
            req: aiohttpが接続を作成するリクエスト。
            traces: aiohttpの接続追跡情報。
            timeout: aiohttpが渡す接続タイムアウト設定。

        Returns:
            検証済み接続のプロトコル。

        Raises:
            ValueError: 接続前に登録時の待受を確認できない場合。
            aiohttp.ClientConnectionError: 接続後に待受プロセスの終了・交代を検出した場合。
        """
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
    """認証情報を除いたHTTP要求を本人のloopback APIへ転送する。

    Args:
        inventory: 公開対象の待受プロセスが登録時と同一か確認する接続先。
        target: 登録時の所有者・プロセス・ソケットを含む接続先情報。
        method: 使用するHTTPメソッド。
        path: 公開先APIへ転送する、/から始まる相対パス。
        headers: 転送元または転送先のHTTPヘッダー。
        body: 転送するリクエスト本文。
        timeout: 処理完了を待つ上限時間。単位は秒。

    Yields:
        本文をストリームで読み取れるaiohttpのHTTP応答。

    Raises:
        ValueError: 相対パスが不正、または登録時の待受プロセスを確認できない場合。
    """
    if not path.startswith("/") or any(c in path for c in "\r\n\x00"):
        raise ValueError("API path is invalid")

    connector = VerifiedConnector(inventory, target)
    # 環境変数のHTTPプロキシやリダイレクトを経由せず、登録されたloopbackへ接続する。
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
