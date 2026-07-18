"""管理画面リダイレクトとSpawn後の遷移を提供する。"""

from jupyterhub.handlers.pages import SpawnHandler
from jupyterhub.utils import url_path_join
from tornado import web

from ..common import BaseHandler
from ..users import _hpc_is_portal_admin

class HpcAdminRedirectHandler(BaseHandler):
    """JupyterHub 標準 /hub/admin を Linux ユーザー管理へ転送する"""

    @web.authenticated
    async def get(self, *args, **kwargs):
        """標準管理画面から権限に応じたポータル画面へ転送する。

        Args:
            *args: JupyterHubから渡される未使用の位置引数。
            **kwargs: JupyterHubから渡される未使用のキーワード引数。
        """
        if _hpc_is_portal_admin(self.current_user):
            self.redirect(url_path_join(self.hub.base_url, "admin", "users"))
        else:
            self.redirect(url_path_join(self.hub.base_url, "home"))

class HpcSpawnHandler(SpawnHandler):
    """起動要求後の遷移先をポータルHomeへ変更する。"""

    def _get_pending_url(self, user, server_name):
        """標準Pending画面ではなく、状態監視機能を持つHomeを返す。

        Args:
            user: 起動対象のJupyterHubユーザー。
            server_name: 起動対象のnamed server名。

        Returns:
            ポータルHomeのURL。
        """
        return url_path_join(self.hub.base_url, "home")
