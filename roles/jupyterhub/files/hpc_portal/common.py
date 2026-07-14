"""HPCポータル拡張で共有する依存関係と実行時定数を提供する。"""

import asyncio
import html
import json
import logging
import secrets
import threading
import time

from batchspawner import SlurmSpawner
from jupyterhub.handlers.base import BaseHandler
from jupyterhub.handlers.login import LoginHandler, LogoutHandler
from jupyterhub.proxy import ConfigurableHTTPProxy, Proxy
from jupyterhub.utils import url_escape_path, url_path_join
from tornado import web
from tornado.httputil import url_concat

try:
    from jupyterhub._xsrf_utils import (
        _get_xsrf_token_cookie,
        _needs_check_xsrf,
        _set_xsrf_cookie,
        check_xsrf_cookie as _jh_check_xsrf_cookie,
    )
except ImportError:
    from jupyterhub.handlers.base import _set_xsrf_cookie

    def _get_xsrf_token_cookie(handler):
        """互換対象のJupyterHubでXSRF Cookie未取得を表す。"""
        return (None, None)

    def _needs_check_xsrf(handler):
        """互換対象のJupyterHubでは常にXSRF検証を要求する。"""
        return True

    def _jh_check_xsrf_cookie(handler):
        """Handler自身の実装を使ってXSRF Cookieを検証する。"""
        return handler.check_xsrf_cookie()

try:
    from jupyterhub.metrics import CHECK_ROUTES_DURATION_SECONDS
except Exception:  # noqa: S110

    class _DummyMetric:
        """メトリクスAPIがないJupyterHub向けの代替実装。"""

        def observe(self, _t):
            """計測値を受け取り、互換性維持のため何もしない。"""
            pass

    CHECK_ROUTES_DURATION_SECONDS = _DummyMetric()


try:
    from jupyterhub.proxy import _one_at_a_time
except Exception:  # noqa: S110

    def _one_at_a_time(method):
        """排他デコレーターがない場合に元のメソッドを返す。"""
        return method

try:
    from jupyterhub.utils import subdomain_hook_idna as _default_subdomain_hook
except ImportError:
    from jupyterhub.utils import subdomain_hook_legacy as _default_subdomain_hook
import contextvars
import nest_asyncio
import os
import pwd
import psutil
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import urlparse

from .runtime import c
from .settings import (
    HPC_BATCH_EXECHOST_EXP,
    HPC_GPU_COUNT,
    HPC_JOB_DNS_DOMAIN,
    HPC_OLLAMA_ALLOWED_CPUS,
    HPC_OLLAMA_ALLOWED_MEMORY,
    HPC_OLLAMA_DEFAULT_CPUS,
    HPC_OLLAMA_DEFAULT_MEMORY,
    HPC_OLLAMA_MODELS_DIR,
    HPC_OLLAMA_PORT,
    HPC_OLLAMA_RUNTIME,
    HPC_OLLAMA_VERSION,
    HPC_JUPYTER_UBUNTU_VERSION,
    HPC_OPENWEBUI_VERSION,
    HPC_PORTAL_ADMIN_USERS,
    HPC_PORTAL_GRANT_SUDO,
    HPC_PORTAL_PROTECTED_USERS,
    HPC_PORTAL_SUDO_GROUP,
    HPC_PORTAL_USER_MIN_UID,
    HPC_PUBLIC_DOMAIN,
    HPC_PUBLIC_SCHEME,
    JUPYTERHUB_HUB_PORT,
    JUPYTERHUB_PORT,
    SLURM_NODE_NAME,
)

nest_asyncio.apply()

# Spawn中だけJOBID由来のホスト名をOAuth URL計算へ渡す。
_oauth_job_host_ctx = contextvars.ContextVar("hpc_oauth_job_host", default=None)

HPC_RESOURCE_METER_JS = "/etc/jupyterhub/static/hpc-resource-meter.js"
HPC_APP_STATUS_JS = "/etc/jupyterhub/static/hpc-app-status.js"
HPC_PORTAL_CSS = "/etc/jupyterhub/static/hpc-portal.css"
HPC_LITELLM_INTERNAL_BASE_URL = os.environ.get(
    "HPC_LITELLM_INTERNAL_BASE_URL", "http://127.0.0.1:4000"
).rstrip("/")
HPC_LITELLM_PUBLIC_BASE_URL = os.environ.get("HPC_LITELLM_PUBLIC_BASE_URL", "")
HPC_LITELLM_ADMIN_URL = os.environ.get("HPC_LITELLM_ADMIN_URL", "")
HPC_LITELLM_MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY", "")
HPC_OLLAMA_API_BASE = os.environ.get(
    "HPC_OLLAMA_API_BASE", f"http://127.0.0.1:{HPC_OLLAMA_PORT}"
).rstrip("/")
OPENWEBUI_LITELLM_BASE_URL = os.environ.get("OPENWEBUI_LITELLM_BASE_URL", "")
OPENWEBUI_LITELLM_KEY_DIR = os.environ.get(
    "OPENWEBUI_LITELLM_KEY_DIR", "/etc/litellm/openwebui-keys"
)
HPC_LITELLM_LOG = logging.getLogger("jupyterhub.hpc-litellm")
_HPC_OPENWEBUI_KEY_LOCKS: dict[str, asyncio.Lock] = {}
_HPC_EXTERNAL_API_KEY_LOCKS = {}
_HPC_EXTERNAL_API_KEY_LOCKS_GUARD = threading.Lock()
