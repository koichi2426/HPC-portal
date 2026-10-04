"""リソース状態とポータル静的ファイルのHTTP応答。"""

import asyncio
import os
import time

from jupyterhub.handlers.base import BaseHandler
from tornado import web

from hpc_portal.entrypoints.dependencies import get_dependencies
from hpc_portal.infrastructure.filesystem.static_asset_versions import (
    HPC_PORTAL_CSS,
    HPC_PORTAL_JS_DIR,
    HPC_PORTAL_JS_FILES,
)
from hpc_portal.presentation.schemas.resources import HpcResourceSnapshot


class HpcResourceStatusHandler(BaseHandler):
    """ホーム/起動フォームのリソースメーターを定期更新するための JSON API"""

    @web.authenticated
    async def get(self):
        """現在の空きリソースをキャッシュ無効のJSONで返す。"""
        self.set_header("Cache-Control", "no-store, no-cache, must-revalidate")
        payload = await asyncio.to_thread(get_dependencies().resources.execute)
        payload["updated_at"] = time.time()
        self.write(HpcResourceSnapshot.model_validate(payload).model_dump())


class HpcPortalJsHandler(BaseHandler):
    """HPCポータルの責務別JavaScriptを配信する。"""

    async def get(self, filename: str):
        """許可済みJavaScriptファイルを返す。

        Args:
            filename: URLで指定されたJavaScriptファイル名。
        """
        self.set_header("Content-Type", "application/javascript; charset=UTF-8")
        self.set_header("Cache-Control", "public, max-age=300")
        self.set_header("X-Content-Type-Options", "nosniff")
        if filename not in HPC_PORTAL_JS_FILES:
            raise web.HTTPError(404)
        try:
            path = os.path.join(HPC_PORTAL_JS_DIR, filename)
            with open(path, encoding="utf-8") as f:
                self.write(f.read())
        except OSError:
            self.set_status(404)
            self.write("/* hpc portal JavaScript not found */")


class HpcPortalCssHandler(BaseHandler):
    """HPC ポータル共通スタイルシート（/hub/static が使えない環境向け）"""

    async def get(self):
        """HPCポータル共通CSSを返す。"""
        self.set_header("Content-Type", "text/css; charset=UTF-8")
        self.set_header("Cache-Control", "public, max-age=300")
        try:
            with open(HPC_PORTAL_CSS, encoding="utf-8") as f:
                self.write(f.read())
        except OSError:
            self.set_status(404)
            self.write("/* hpc-portal.css not found */")
