"""アプリ起動フォームの生成と入力値変換を提供する。"""

from tornado import web

from hpc_portal.bootstrap.container import get_container
from hpc_portal.domain.errors import UseCaseError
from hpc_portal.infrastructure.config.settings import (
    HPC_JOB_DNS_DOMAIN,
    HPC_JUPYTER_UBUNTU_VERSION,
    HPC_OLLAMA_ALLOWED_CONTEXT_LENGTHS,
    HPC_OLLAMA_ALLOWED_CPUS,
    HPC_OLLAMA_ALLOWED_KEEP_ALIVE,
    HPC_OLLAMA_ALLOWED_KV_CACHE_TYPES,
    HPC_OLLAMA_ALLOWED_MAX_LOADED_MODELS,
    HPC_OLLAMA_ALLOWED_MAX_QUEUE,
    HPC_OLLAMA_ALLOWED_MEMORY,
    HPC_OLLAMA_ALLOWED_PARALLEL,
    HPC_OLLAMA_DEFAULT_CONTEXT_LENGTH,
    HPC_OLLAMA_DEFAULT_CPUS,
    HPC_OLLAMA_DEFAULT_FLASH_ATTENTION,
    HPC_OLLAMA_DEFAULT_KEEP_ALIVE,
    HPC_OLLAMA_DEFAULT_KV_CACHE_TYPE,
    HPC_OLLAMA_DEFAULT_MAX_LOADED_MODELS,
    HPC_OLLAMA_DEFAULT_MAX_QUEUE,
    HPC_OLLAMA_DEFAULT_MEMORY,
    HPC_OLLAMA_DEFAULT_PARALLEL,
    HPC_OLLAMA_VERSION,
    HPC_OPENWEBUI_VERSION,
    HPC_PUBLIC_SCHEME,
)
from hpc_portal.infrastructure.filesystem.static_asset_versions import (
    HPC_STATIC_VERSIONS,
)
from hpc_portal.infrastructure.linux.user_account_gateway import is_portal_admin
from hpc_portal.presentation.job_form_renderer import (
    JobFormSettings,
    render_options_form,
)
from hpc_portal.presentation.ollama_presenter import shared_ollama_detail_context


def make_options_form(spawner):
    """Spawnerの状態に合わせてアプリ起動フォームを生成する。

    Args:
        spawner: フォームを表示するSpawner。

    Returns:
        JupyterHubへ渡すHTML文字列。
    """
    disk_path = (
        getattr(spawner, "notebook_dir", "")
        or getattr(spawner, "homedir", "")
        or "/home"
    )
    dependencies = get_container()
    portal_admin = is_portal_admin(spawner.user)
    return render_options_form(
        spawner,
        resource=dependencies.resources.execute(disk_path),
        portal_admin=portal_admin,
        shared=shared_ollama_detail_context() if portal_admin else {},
        recommendations=dependencies.jobs.policy.app_resource_recommendations(),
        shared_ollama_gpu_label=dependencies.ollama.backend.gpu_label(),
        static_versions=HPC_STATIC_VERSIONS,
        settings=JobFormSettings(
            jupyter_ubuntu_version=HPC_JUPYTER_UBUNTU_VERSION,
            ollama_allowed_context_lengths=HPC_OLLAMA_ALLOWED_CONTEXT_LENGTHS,
            ollama_allowed_cpus=HPC_OLLAMA_ALLOWED_CPUS,
            ollama_allowed_keep_alive=HPC_OLLAMA_ALLOWED_KEEP_ALIVE,
            ollama_allowed_kv_cache_types=HPC_OLLAMA_ALLOWED_KV_CACHE_TYPES,
            ollama_allowed_max_loaded_models=HPC_OLLAMA_ALLOWED_MAX_LOADED_MODELS,
            ollama_allowed_max_queue=HPC_OLLAMA_ALLOWED_MAX_QUEUE,
            ollama_allowed_memory=HPC_OLLAMA_ALLOWED_MEMORY,
            ollama_allowed_parallel=HPC_OLLAMA_ALLOWED_PARALLEL,
            ollama_default_context_length=HPC_OLLAMA_DEFAULT_CONTEXT_LENGTH,
            ollama_default_cpus=HPC_OLLAMA_DEFAULT_CPUS,
            ollama_default_flash_attention=HPC_OLLAMA_DEFAULT_FLASH_ATTENTION,
            ollama_default_keep_alive=HPC_OLLAMA_DEFAULT_KEEP_ALIVE,
            ollama_default_kv_cache_type=HPC_OLLAMA_DEFAULT_KV_CACHE_TYPE,
            ollama_default_max_loaded_models=HPC_OLLAMA_DEFAULT_MAX_LOADED_MODELS,
            ollama_default_max_queue=HPC_OLLAMA_DEFAULT_MAX_QUEUE,
            ollama_default_memory=HPC_OLLAMA_DEFAULT_MEMORY,
            ollama_default_parallel=HPC_OLLAMA_DEFAULT_PARALLEL,
            ollama_version=HPC_OLLAMA_VERSION,
            openwebui_version=HPC_OPENWEBUI_VERSION,
            public_scheme=HPC_PUBLIC_SCHEME,
            job_dns_domain=HPC_JOB_DNS_DOMAIN,
        ),
    )


def apply_user_options(spawner, user_options):
    """起動直前にuser_optionsを検証し直し、Spawnerの要求リソースと起動設定へ反映する。

    フォームを通らないAPIからの起動でも、利用者の指定項目だけから
    #SBATCH行などをサーバー側で組み立て直す。

    Args:
        spawner: 設定対象のSpawner。
        user_options: フォーム・APIから渡された、または前回保存された設定辞書。

    Raises:
        web.HTTPError: 指定が不正、または空きリソースを超える場合。
    """
    try:
        options = get_container().jobs.options_from_form.revalidate(user_options)
    except UseCaseError as exc:
        raise web.HTTPError(400, str(exc)) from exc

    # 送られた値は使わず、組み立て直した設定だけを起動と保存に使う。
    spawner.user_options = options
    orm_spawner = getattr(spawner, "orm_spawner", None)
    if orm_spawner is not None:
        orm_spawner.user_options = dict(options)
    spawner.req_nprocs = options["nprocs"]
    spawner.req_memory = options["memory"]
    spawner.req_runtime = options["runtime"]
    if options["app_choice"] == "open-webui":
        # BatchSpawner の service_url が :0 になると pending から進めないため、
        # server_name ベースで Hub 側ポートを事前確定する
        seed = str(getattr(spawner, "name", "") or "")
        h = sum(ord(c) for c in seed) % 20000
        spawner.port = 20000 + h
