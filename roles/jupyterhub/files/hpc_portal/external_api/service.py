"""Lazy services and recovery; user applications are never started or stopped."""
import asyncio
import logging

from jupyterhub.app import JupyterHub
from tornado.ioloop import IOLoop, PeriodicCallback

from .access import AccessVerifier
from .cloudflare import Cloudflare
from .config import Config
from .credentials import Credentials, HubTokens
from .inventory import Inventory
from .network import NetworkGuard
from .publications import Publications
from .store import Store

log = logging.getLogger("jupyterhub.external-api")
_service = None
_callback = None
_sync_lock = asyncio.Lock()


def service():
    global _service
    if _service is None:
        config = Config.from_env()
        config.validate()
        store = Store(config.state_dir)
        credentials = Credentials(store, Cloudflare(config), HubTokens(JupyterHub.instance()), config)
        inventory = Inventory(config)
        publications = Publications(store, config, credentials, inventory,
                                    NetworkGuard(store, inventory), AccessVerifier(config))
        _service = credentials, publications
    return _service


async def sync():
    if _sync_lock.locked():
        return
    async with _sync_lock:
        try:
            from ..users import _hpc_linux_users_snapshot
            credentials, publications = service()
            rows = _hpc_linux_users_snapshot()
            usernames = {row["username"] for row in rows}
            for name in credentials.store.names("credentials"):
                record = credentials.store.get("credentials", name)
                if name not in usernames or record.get("state") == "revoking":
                    try:
                        await disable(name)
                    except Exception:
                        log.warning("External API revocation pending for %s", name)
            for row in rows:
                try:
                    user = await credentials.hub.user(row["username"])
                    await credentials.ensure(user)
                except Exception:
                    log.warning("External API issuance pending for %s", row["username"])
            for key in publications.store.names("publications"):
                record = publications.store.get("publications", key)
                try:
                    await publications.refresh(record)
                except Exception:
                    log.warning("External API publication recovery pending for %s", key)
            await publications.guard.reconcile()
        except Exception:
            # Remote error bodies, application responses and secrets are not logged.
            log.warning("External API synchronization requires operator configuration")


def start_background():
    global _callback
    if Config.from_env().enabled and _callback is None:
        _callback = PeriodicCallback(sync, 30000)
        _callback.start()
        async def initial():
            await asyncio.sleep(5)
            await sync()
        IOLoop.current().spawn_callback(initial)


async def provision(username):
    if Config.from_env().enabled:
        credentials, _ = service()
        await credentials.ensure(await credentials.hub.user(username))


async def disable(username):
    if Config.from_env().enabled:
        credentials, publications = service()
        async with credentials.lock(username):
            record = credentials.store.get("credentials", username)
            if record:
                record.update(enabled=False, state="revoking")
                credentials.store.put("credentials", username, record)
                credentials.hub.revoke(record.get("hub_token_id"))
        try:
            await publications.unpublish_all(username)
        finally:
            await credentials.disable(username)


async def deleted(username):
    if Config.from_env().enabled:
        credentials, publications = service()
        async with credentials.lock(username):
            record = credentials.store.get("credentials", username)
            if record and record.get("state") != "disabled":
                raise ValueError("資格情報の失効が未完了です")
            for key in publications.store.names("publications"):
                if key.startswith(username + "/"):
                    entry = publications.store.get("publications", key)
                    await publications._remove_remote(entry)
                    publications.store.delete("publications", key)
            credentials.store.delete("credentials", username)
