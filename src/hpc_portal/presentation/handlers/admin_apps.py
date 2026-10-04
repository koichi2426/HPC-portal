"""管理者向け起動中ジョブ一覧のHTTP応答。"""

import asyncio
import time

from jupyterhub.handlers.base import BaseHandler
from tornado import web

from hpc_portal.infrastructure.linux.user_account_gateway import is_portal_admin
from hpc_portal.infrastructure.slurm.slurm_client import admin_apps_snapshot
from hpc_portal.presentation.schemas import HpcAdminAppsResponse


class HpcAdminAppsApiHandler(BaseHandler):
    """管理者へポータル由来の起動中Slurmアプリ一覧を返す。"""

    @web.authenticated
    async def get(self):
        """アプリの割当と最大RSSをJSONで返す。

        Raises:
            web.HTTPError: ポータル管理者ではない場合。
        """
        if not is_portal_admin(self.current_user):
            raise web.HTTPError(403, "管理者のみアクセスできます")
        apps, error = await asyncio.to_thread(admin_apps_snapshot)
        self.set_header("Cache-Control", "no-store")
        response = HpcAdminAppsResponse.model_validate(
            {"apps": apps, "error": error, "updated_at": time.time()}
        )
        self.write(response.model_dump())
