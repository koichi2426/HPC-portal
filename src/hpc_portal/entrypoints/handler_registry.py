"""HPCポータルのHTTP HandlerをJupyterHubへ登録する。"""

import jupyterhub.handlers as _jh_handlers
import jupyterhub.handlers.pages as _jh_pages_handlers
from jupyterhub.handlers.pages import SpawnHandler
from traitlets.config.loader import LazyConfigValue

from hpc_portal.presentation.handlers.admin_apps import HpcAdminAppsApiHandler
from hpc_portal.presentation.handlers.admin_users import (
    HpcAdminUsersApiHandler,
    HpcAdminUsersPageHandler,
)
from hpc_portal.presentation.handlers.api_gateway import ApiGateway
from hpc_portal.presentation.handlers.external_api import (
    ApiPorts,
    ApiPublications,
    ApiPublicationsPage,
    ExternalApiCredentials,
    ExternalApiPage,
)
from hpc_portal.presentation.handlers.jobs import (
    HpcAppDetailHandler,
    HpcAppMemoryStatusHandler,
    HpcNewApplicationHandler,
    HpcOpenWebuiVersionHandler,
)
from hpc_portal.presentation.handlers.llm_api import (
    HpcLlmApiApiHandler,
    HpcLlmApiPageHandler,
)
from hpc_portal.presentation.handlers.password import (
    HpcPasswordApiHandler,
    HpcPasswordPageHandler,
)
from hpc_portal.presentation.handlers.resources import (
    HpcPortalCssHandler,
    HpcPortalJsHandler,
    HpcResourceStatusHandler,
)
from hpc_portal.presentation.handlers.spawn import (
    HpcAdminRedirectHandler,
    HpcSpawnHandler,
)
from hpc_portal.presentation.handlers.ssh_access import SshAccessPage, SshCredentials


def register_handlers(config) -> None:
    """標準Handlerの差し替えとポータル固有ルートの追加を一度だけ行う。

    Args:
        config: ハンドラーと静的ファイルを登録するJupyterHubのtraitlets設定。
    """

    for handlers in (
        _jh_pages_handlers.default_handlers,
        _jh_handlers.default_handlers,
    ):
        for index, (route, handler_class) in enumerate(handlers):
            if route == "/admin":
                handlers[index] = (route, HpcAdminRedirectHandler)
            elif handler_class is SpawnHandler:
                handlers[index] = (route, HpcSpawnHandler)

    portal_handlers = [
        (r"/new", HpcNewApplicationHandler),
        (r"/external-api", ExternalApiPage),
        (r"/external-api/credentials", ExternalApiCredentials),
        (r"/ssh-access", SshAccessPage),
        (r"/ssh-access/credentials", SshCredentials),
        (r"/api-publications", ApiPublicationsPage),
        (r"/api-publications/api", ApiPublications),
        (r"/api-publications/ports", ApiPorts),
        (r"/user-api/([^/]+)/([a-z][a-z0-9-]{0,47})(?:/(.*))?", ApiGateway),
        (r"/hpc-js/([a-z0-9-]+\.js)", HpcPortalJsHandler),
        (r"/hpc-portal.css", HpcPortalCssHandler),
        (r"/hpc-resource-status", HpcResourceStatusHandler),
        (r"/hpc-app-memory", HpcAppMemoryStatusHandler),
        (r"/apps/([^/]+)/version", HpcOpenWebuiVersionHandler),
        (r"/apps/([^/]+)", HpcAppDetailHandler),
        (r"/llm-api/api", HpcLlmApiApiHandler),
        (r"/llm-api", HpcLlmApiPageHandler),
        (r"/account/password/api", HpcPasswordApiHandler),
        (r"/account/password", HpcPasswordPageHandler),
        (r"/admin/apps/api", HpcAdminAppsApiHandler),
        (r"/admin/users/api", HpcAdminUsersApiHandler),
        (r"/admin/users", HpcAdminUsersPageHandler),
    ]
    existing = config.JupyterHub.get("extra_handlers", [])
    if isinstance(existing, LazyConfigValue):
        existing = existing.get_value([])
    portal_routes = {route for route, _handler in portal_handlers}
    config.JupyterHub.extra_handlers = [
        entry for entry in existing if entry[0] not in portal_routes
    ] + portal_handlers
