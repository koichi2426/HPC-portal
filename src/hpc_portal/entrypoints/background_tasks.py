"""起動時にAPI同期を登録する。操作手順はusecaseに置く。"""

import asyncio
import logging

from tornado.ioloop import IOLoop, PeriodicCallback

from hpc_portal.entrypoints.dependencies import get_external_api

log = logging.getLogger("jupyterhub.external-api")
_callback = None


async def synchronize_external_api():
    try:
        usecase = get_external_api()
        if usecase:
            await usecase.synchronize()
    except Exception:
        # Never include remote error bodies or secrets in synchronization logs.
        log.warning("External API synchronization requires operator configuration")


def start_background_tasks(enabled):
    global _callback
    if not enabled or _callback is not None:
        return
    _callback = PeriodicCallback(synchronize_external_api, 30000)
    _callback.start()

    async def initial_sync():
        await asyncio.sleep(5)
        await synchronize_external_api()

    IOLoop.current().spawn_callback(initial_sync)
