"""本人専用のHubトークンとCloudflare認証を照合し、登録済みAPIへ転送する。"""

import asyncio
from urllib.parse import quote

import aiohttp
from jupyterhub.apihandlers.base import APIHandler
from tornado import web
from tornado.iostream import StreamClosedError

from hpc_portal.bootstrap.container import get_external_api
from hpc_portal.domain.errors import UseCaseError
from hpc_portal.infrastructure.http.api_relay import clean_headers


class ApiGateway(APIHandler):
    SUPPORTED_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
    _semaphore = None

    async def invoke(self, username, name, path=""):
        """本人認証・公開状態・同時転送数を確認し、API応答をストリームで返す。

        Args:
            username: 対象のLinuxユーザー名。
            name: 呼び出す登録APIの識別名。
            path: 登録APIへ転送する相対パス。

        Raises:
            web.HTTPError: 認証・公開状態・転送数・接続先の条件を満たさない場合。
        """
        token = self.get_token()
        if (
            not token
            or not token.user
            or not self.current_user
            or self.current_user.name != username
        ):
            raise web.HTTPError(403)

        started = False
        try:
            usecase = get_external_api()
            if usecase is None:
                raise web.HTTPError(503)
            try:
                app = await usecase.authorize_request.execute(
                    self.current_user,
                    token,
                    username,
                    name,
                    self.request.headers,
                    len(self.request.body),
                )
            except UseCaseError as exc:
                status = {
                    "forbidden": 403,
                    "missing": 404,
                    "unavailable": 503,
                    "too_large": 413,
                }.get(exc.code, 400)
                raise web.HTTPError(status) from None

            target = "/" + quote(path or "", safe="/@-._~")
            if self.request.query:
                target += "?" + self.request.query
            if self._semaphore is None:
                type(self)._semaphore = asyncio.Semaphore(usecase.config.concurrency)

            # 同時転送数を制限し、枠を待つ時間は2秒までにする。
            try:
                await asyncio.wait_for(self._semaphore.acquire(), 2)
            except TimeoutError:
                raise web.HTTPError(429) from None

            try:
                async with usecase.relay.request(
                    usecase.listeners,
                    app["target"],
                    self.request.method,
                    target,
                    self.request.headers,
                    self.request.body,
                    timeout=usecase.config.timeout,
                ) as response:
                    self.set_status(response.status)
                    for key, value in clean_headers(
                        response.headers, response=True
                    ).items():
                        if key.lower() == "location":
                            # 外部ホストへの誘導を避け、API内の相対リダイレクトだけを書き換える。
                            if not value.startswith("/") or value.startswith("//"):
                                continue
                            value = (
                                f"/hub/user-api/{quote(username, safe='')}/{name}"
                                + value
                            )
                        self.set_header(key, value)

                    self.set_header("Cache-Control", "no-store")
                    # 全応答をメモリに溜めず、受け取った分から呼び出し元へ返す。
                    async for chunk in response.content.iter_chunked(65536):
                        self.write(chunk)
                        started = True
                        await self.flush()
                    self.finish()
            finally:
                self._semaphore.release()
        except StreamClosedError:
            return
        except web.HTTPError:
            raise
        except (OSError, aiohttp.ClientError, TimeoutError, ValueError):
            if started:
                # 本文を返し始めた後はHTTPステータスを変更できないため、接続を閉じる。
                self.request.connection.close()
                return
            raise web.HTTPError(502) from None

    get = post = put = patch = delete = head = options = invoke
