"""API-only gateway: dedicated Hub token plus validated Cloudflare service identity."""
import asyncio
import secrets
from urllib.parse import quote

import aiohttp
from jupyterhub.apihandlers.base import APIHandler
from tornado import web
from tornado.iostream import StreamClosedError

from ..external_api.service import service
from ..external_api.transport import clean_headers, request


class ApiGateway(APIHandler):
    SUPPORTED_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
    _semaphore = None

    async def invoke(self, username, name, path=""):
        token = self.get_token()
        if not token or not token.user or not self.current_user or self.current_user.name != username:
            raise web.HTTPError(403)
        started = False
        try:
            credentials, publications = service()
            try:
                record = credentials.record(self.current_user)
            except (ValueError, KeyError):
                raise web.HTTPError(403) from None
            expected_scope = f"custom:external-api:invoke!user={username}"
            if (not record.get("enabled") or record.get("state") != "ready"
                    or token.id != record.get("hub_token_id") or expected_scope not in token.scopes):
                raise web.HTTPError(403)
            # Raw credentials must still be current: rotation invalidates previously minted JWTs too.
            for header, key in (("CF-Access-Client-ID", "client_id"), ("CF-Access-Client-Secret", "client_secret")):
                if not secrets.compare_digest(self.request.headers.get(header, "").encode(), record[key].encode()):
                    raise web.HTTPError(403)
            try:
                app = publications.get(self.current_user, name)
            except (ValueError, KeyError):
                raise web.HTTPError(404) from None
            if app.get("state") != "published" or app.get("desired") != "published":
                raise web.HTTPError(503)
            try:
                await publications.access.verify(self.request.headers.get("Cf-Access-Jwt-Assertion", ""), app, record)
            except ValueError:
                raise web.HTTPError(403) from None
            if len(self.request.body) > publications.config.body_limit:
                raise web.HTTPError(413)
            await publications.guard.check(app["target"])
            target = "/" + quote(path or "", safe="/@-._~")
            if self.request.query:
                target += "?" + self.request.query
            if self._semaphore is None:
                type(self)._semaphore = asyncio.Semaphore(publications.config.concurrency)
            # Bound queued work as well as running work.
            try:
                await asyncio.wait_for(self._semaphore.acquire(), 2)
            except TimeoutError:
                raise web.HTTPError(429) from None
            try:
                async with request(publications.inventory, app["target"], self.request.method, target,
                    self.request.headers, self.request.body, timeout=publications.config.timeout) as response:
                    self.set_status(response.status)
                    for key, value in clean_headers(response.headers, response=True).items():
                        if key.lower() == "location":
                            if not value.startswith("/") or value.startswith("//"):
                                continue
                            value = f"/hub/user-api/{quote(username, safe='')}/{name}" + value
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
