"""起動時にAPI同期を登録する。操作手順はusecaseに置く。"""

import asyncio
import logging

from tornado.ioloop import IOLoop, PeriodicCallback

from hpc_portal.bootstrap.container import get_external_api

log = logging.getLogger("jupyterhub.external-api")
_callback = None


async def synchronize_external_api():
    """外部APIを同期し、失敗時は秘密値を含まない警告を記録する。"""
    try:
        usecase = get_external_api()
        if usecase:
            await usecase.synchronize.execute()
    except Exception:
        # 外部サービスのエラー本文には秘密値が含まれ得るため、ログへ出さない。
        log.warning("External API synchronization requires operator configuration")


def start_background_tasks(enabled):
    """外部API有効時に、初回同期と30秒間隔の定期同期を一度だけ登録する。

    Args:
        enabled: 外部APIの定期同期を有効にする場合はTrue。
    """
    global _callback
    if not enabled or _callback is not None:
        return
    _callback = PeriodicCallback(synchronize_external_api, 30000)
    _callback.start()

    async def initial_sync():
        """Hubの起動を5秒待ち、外部APIの初回同期を実行する。"""
        await asyncio.sleep(5)
        await synchronize_external_api()

    IOLoop.current().spawn_callback(initial_sync)
