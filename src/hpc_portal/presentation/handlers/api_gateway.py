"""API-only gateway: dedicated Hub token plus validated Cloudflare service identity."""

import asyncio
from urllib.parse import quote

import aiohttp
from jupyterhub.apihandlers.base import APIHandler
from tornado import web
from tornado.iostream import StreamClosedError

from hpc_portal.domain.errors import UseCaseError
from hpc_portal.entrypoints.dependencies import get_external_api
from hpc_portal.infrastructure.http.api_relay import clean_headers


class ApiGateway(APIHandler):
    SUPPORTED_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
    _semaphore = None

    async def invoke(self, username, name, path=""):
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
            # Bound queued work as well as running work.
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
                            if not value.startswith("/") or value.startswith("//"):
                                continue
                            value = (
                                f"/hub/user-api/{quote(username, safe='')}/{name}"
                                + value
                            )
                        self.set_header(key, value)
                    self.set_header("Cache-Control", "no-store")
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
                self.request.connection.close()
                return
            raise web.HTTPError(502) from None

    get = post = put = patch = delete = head = options = invoke
