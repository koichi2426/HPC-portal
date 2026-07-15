"""HPCポータルのHTTP HandlerをJupyterHubへ登録する。"""

import jupyterhub.handlers as _jh_handlers
import jupyterhub.handlers.pages as _jh_pages_handlers
from jupyterhub.handlers.pages import SpawnHandler

from ..apps import (
    HpcAppDetailHandler,
    HpcNewApplicationHandler,
    HpcOpenWebuiVersionHandler,
)
from ..common import c
from ..resources import (
    HpcPortalCssHandler,
    HpcPortalJsHandler,
    HpcResourceStatusHandler,
)
from .admin_apps import HpcAdminAppsApiHandler
from .admin_users import HpcAdminUsersApiHandler, HpcAdminUsersPageHandler
from .llm_api import HpcLlmApiApiHandler, HpcLlmApiPageHandler
from .password import HpcPasswordApiHandler, HpcPasswordPageHandler
from .spawn import HpcAdminRedirectHandler, HpcSpawnHandler

_REGISTERED = False


def register_handlers() -> None:
    """標準Handlerの差し替えとポータル固有ルートの追加を一度だけ行う。"""
    global _REGISTERED
    if _REGISTERED:
        return

    for handlers in (_jh_pages_handlers.default_handlers, _jh_handlers.default_handlers):
        for index, (route, handler_class) in enumerate(handlers):
            if route == "/admin":
                handlers[index] = (route, HpcAdminRedirectHandler)
            elif handler_class is SpawnHandler:
                handlers[index] = (route, HpcSpawnHandler)

    c.JupyterHub.extra_handlers.extend([
        (r"/new", HpcNewApplicationHandler),
        (r"/hpc-js/([a-z0-9-]+\.js)", HpcPortalJsHandler),
        (r"/hpc-portal.css", HpcPortalCssHandler),
        (r"/hpc-resource-status", HpcResourceStatusHandler),
        (r"/apps/([^/]+)/version", HpcOpenWebuiVersionHandler),
        (r"/apps/([^/]+)", HpcAppDetailHandler),
        (r"/llm-api/api", HpcLlmApiApiHandler),
        (r"/llm-api", HpcLlmApiPageHandler),
        (r"/account/password/api", HpcPasswordApiHandler),
        (r"/account/password", HpcPasswordPageHandler),
        (r"/admin/apps/api", HpcAdminAppsApiHandler),
        (r"/admin/users/api", HpcAdminUsersApiHandler),
        (r"/admin/users", HpcAdminUsersPageHandler),
    ])
    _REGISTERED = True
