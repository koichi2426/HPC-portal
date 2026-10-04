"""JupyterHubへポータルの設定・画面・起動処理を登録する。"""

import nest_asyncio
from tornado import web

from hpc_portal.domain.errors import UseCaseError
from hpc_portal.entrypoints.background_tasks import start_background_tasks
from hpc_portal.entrypoints.dependencies import get_dependencies
from hpc_portal.entrypoints.handler_registry import register_handlers
from hpc_portal.infrastructure.config.external_api_settings import (
    ExternalApiSettings as ExternalApiConfig,
)
from hpc_portal.infrastructure.config.settings import (
    HPC_BATCH_EXECHOST_EXP,
    HPC_JOB_DNS_DOMAIN,
    HPC_JUPYTER_UBUNTU_VERSION,
    HPC_LITELLM_ADMIN_URL,
    HPC_LITELLM_PUBLIC_BASE_URL,
    HPC_OLLAMA_VERSION,
    HPC_OPENWEBUI_VERSION,
    HPC_PORTAL_ADMIN_USERS,
    HPC_PUBLIC_DOMAIN,
    HPC_PUBLIC_SCHEME,
    JUPYTERHUB_HUB_PORT,
    JUPYTERHUB_PORT,
    OPENWEBUI_LITELLM_BASE_URL,
)
from hpc_portal.infrastructure.filesystem.static_asset_versions import (
    HPC_STATIC_VERSIONS,
)
from hpc_portal.infrastructure.jupyterhub.configurable_http_proxy import (
    HpcConfigurableHTTPProxy,
    install_proxy_hooks,
)
from hpc_portal.infrastructure.jupyterhub.framework_compatibility import (
    _default_subdomain_hook,
)
from hpc_portal.infrastructure.jupyterhub.oauth_context import _oauth_job_host_ctx
from hpc_portal.infrastructure.jupyterhub.session_authenticator import (
    install_session_hooks,
)
from hpc_portal.infrastructure.jupyterhub.slurm_spawner import HPCSlurmSpawner
from hpc_portal.infrastructure.slurm.batch_script_builder import build_batch_script
from hpc_portal.presentation.job_form import apply_user_options, make_options_form
from hpc_portal.presentation.job_presenter import user_memory_overuse
from hpc_portal.presentation.ollama_presenter import shared_ollama_detail_context


def hpc_subdomain_hook(name, domain, kind):
    host = _oauth_job_host_ctx.get()
    if kind == "user" and host:
        return host
    if kind == "user":
        return HPC_PUBLIC_DOMAIN
    return _default_subdomain_hook(name, HPC_JOB_DNS_DOMAIN, kind)


def options_from_form(formdata):
    try:
        return get_dependencies().jobs.options_from_form(formdata)
    except UseCaseError as exc:
        raise web.HTTPError(400, str(exc)) from exc


def configure_jupyterhub(c):
    nest_asyncio.apply()
    install_session_hooks()
    install_proxy_hooks()
    dependencies = get_dependencies()
    c.JupyterHub.bind_url = f"http://0.0.0.0:{JUPYTERHUB_PORT}"
    c.JupyterHub.hub_bind_url = f"http://127.0.0.1:{JUPYTERHUB_HUB_PORT}/hub/"
    c.JupyterHub.hub_connect_url = f"http://127.0.0.1:{JUPYTERHUB_HUB_PORT}/hub/"
    c.JupyterHub.hub_ip = "127.0.0.1"
    c.JupyterHub.hub_connect_ip = "127.0.0.1"
    # cloudflared → 127.0.0.1:8000 経由の X-Forwarded-* を信頼しないと、Proto/Port がブレて
    # /hub/user/... ↔ https://gx10.../user/... のリダイレクトループになる（journal に Redirect loop が出る）
    c.JupyterHub.trusted_downstream_ips = ["127.0.0.1", "::1"]
    c.JupyterHub.default_url = "/hub/home"
    c.JupyterHub.template_paths = ["/etc/jupyterhub/templates"]
    c.JupyterHub.allow_named_servers = True
    # Hubの再起動時もBatchSpawnerが投入したSlurmジョブは停止しない。
    c.JupyterHub.cleanup_servers = False

    c.Authenticator.allow_all = True
    # Linuxユーザーを正とし、OS側で削除済みのユーザーをHub DBへ残さない。
    c.Authenticator.delete_invalid_users = True

    c.Authenticator.admin_users = set(HPC_PORTAL_ADMIN_USERS)

    # サブドメイン方式: ゾーンは <base-domain>（job<N>.<base-domain> を CHP が受ける）
    c.JupyterHub.subdomain_host = f"{HPC_PUBLIC_SCHEME}://{HPC_JOB_DNS_DOMAIN}"
    # Hub のブラウザ向け URL（ログイン・ダッシュは <hub-subdomain>.<base-domain>）
    c.JupyterHub.public_url = f"{HPC_PUBLIC_SCHEME}://{HPC_PUBLIC_DOMAIN}/"
    c.JupyterHub.subdomain_hook = hpc_subdomain_hook
    # gx10.<zone> と job<id>.<zone> 間で認証/ XSRF cookie を共有
    # traitlets の LazyConfigValue では setdefault が使えないため dict を直接代入する
    c.JupyterHub.tornado_settings = {
        "headers": {
            "Content-Security-Policy": f"frame-ancestors 'self' https://*.{HPC_JOB_DNS_DOMAIN}",
        },
        "cookie_options": {
            "domain": f".{HPC_JOB_DNS_DOMAIN}",
            "secure": True,
            "samesite": "lax",
        },
    }

    c.JupyterHub.template_vars = {
        "hpc_public_domain": HPC_PUBLIC_DOMAIN,
        "hpc_job_dns_domain": HPC_JOB_DNS_DOMAIN,
        "hpc_public_scheme": HPC_PUBLIC_SCHEME,
        "hpc_portal_admin_users": sorted(HPC_PORTAL_ADMIN_USERS),
        "hpc_litellm_public_base_url": HPC_LITELLM_PUBLIC_BASE_URL,
        "hpc_litellm_admin_url": HPC_LITELLM_ADMIN_URL,
        "hpc_openwebui_version": HPC_OPENWEBUI_VERSION,
        "hpc_jupyter_ubuntu_version": HPC_JUPYTER_UBUNTU_VERSION,
        "hpc_static_versions": HPC_STATIC_VERSIONS,
        "hpc_ollama_version": HPC_OLLAMA_VERSION.removeprefix("v"),
        "hpc_resource_snapshot": dependencies.resources.snapshot,
        "hpc_memory_overuse": user_memory_overuse,
        "hpc_shared_ollama_detail": shared_ollama_detail_context,
    }
    c.JupyterHub.proxy_class = HpcConfigurableHTTPProxy
    c.JupyterHub.spawner_class = HPCSlurmSpawner
    c.HPCSlurmSpawner.portal_dependencies_provider = get_dependencies
    c.HPCSlurmSpawner.options_form = make_options_form
    c.HPCSlurmSpawner.options_from_form = options_from_form
    c.HPCSlurmSpawner.state_exechost_exp = HPC_BATCH_EXECHOST_EXP
    c.HPCSlurmSpawner.ip = "127.0.0.1"
    c.HPCSlurmSpawner.req_nprocs = "2"
    c.HPCSlurmSpawner.req_memory = "4G"
    c.HPCSlurmSpawner.req_runtime = "02:00:00"
    c.HPCSlurmSpawner.req_partition = "debug"
    c.HPCSlurmSpawner.batch_script = build_batch_script()
    c.Spawner.start_timeout = 300
    c.Spawner.cmd = ["jupyterhub-singleuser"]
    c.Spawner.environment = {"OPENWEBUI_LITELLM_BASE_URL": OPENWEBUI_LITELLM_BASE_URL}
    c.Spawner.apply_user_options = apply_user_options
    register_handlers(c)
    external = ExternalApiConfig.from_env()
    c.JupyterHub.template_vars["hpc_external_api_enabled"] = external.enabled
    if external.enabled:
        c.JupyterHub.token_expires_in_max_seconds = 0
        c.JupyterHub.custom_scopes = {
            "custom:external-api:invoke": {
                "description": "Invoke the owner's registered HTTP API apps"
            }
        }
        c.JupyterHub.load_roles = [
            {"name": "user", "scopes": ["self", "custom:external-api:invoke!user"]}
        ]
    start_background_tasks(external.enabled)
