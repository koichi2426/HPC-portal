"""外部接続の実装を組み立て、各usecaseへ渡す。"""

from dataclasses import dataclass, field

from jupyterhub.app import JupyterHub

from hpc_portal.application.usecase.catalog import (
    AccountUseCases,
    ExternalApiUseCases,
    InferenceUseCases,
    JobUseCases,
    LlmUseCases,
)
from hpc_portal.application.usecase.resource_query_usecase import ResourceQueryUseCase
from hpc_portal.domain.accounts.settings import UserManagementSettings
from hpc_portal.domain.jobs.execution_policy import ExecutionPolicy
from hpc_portal.domain.jobs.settings import JobSettings
from hpc_portal.domain.llm.runtime_policy import OllamaResourcePolicy
from hpc_portal.entrypoints.usecase_factory import (
    build_accounts_usecases,
    build_api_usecases,
    build_jobs_usecases,
    build_llm_usecases,
    build_ollama_usecases,
)
from hpc_portal.infrastructure.cloudflare.access_client import CloudflareAccessClient
from hpc_portal.infrastructure.cloudflare.access_token_verifier import AccessVerifier
from hpc_portal.infrastructure.config import settings
from hpc_portal.infrastructure.config.external_api_settings import ExternalApiSettings
from hpc_portal.infrastructure.http import api_relay
from hpc_portal.infrastructure.http.api_health_checker import ApiHealthChecker
from hpc_portal.infrastructure.jupyterhub.token_gateway import HubTokenGateway
from hpc_portal.infrastructure.jupyterhub.user_jobs import HubUserJobs
from hpc_portal.infrastructure.linux.command_runner import LinuxCommandRunner
from hpc_portal.infrastructure.linux.listener_inventory import LinuxListenerInventory
from hpc_portal.infrastructure.linux.port_guard import NftablesPortGuard
from hpc_portal.infrastructure.linux.resource_inventory import LinuxResourceInventory
from hpc_portal.infrastructure.linux.user_account_gateway import LinuxUserAccountGateway
from hpc_portal.infrastructure.litellm.api_client import LiteLlmClient
from hpc_portal.infrastructure.litellm.management_gateway import (
    LiteLlmManagementGateway,
)
from hpc_portal.infrastructure.ollama.ollama_client import OllamaClient
from hpc_portal.infrastructure.openwebui.api_key_store import OpenWebuiKeyStore
from hpc_portal.infrastructure.persistence.api_aggregate_repository import (
    ApiAggregateRepository,
)
from hpc_portal.infrastructure.persistence.api_record_queries import ApiRecordQueries
from hpc_portal.infrastructure.persistence.encrypted_record_store import (
    EncryptedRecordStore,
)


@dataclass
class PortalDependencies:
    users: AccountUseCases
    jobs: JobUseCases
    llm: LlmUseCases
    ollama: InferenceUseCases
    resources: ResourceQueryUseCase
    openwebui_key_locks: dict = field(default_factory=dict)
    external_api: ExternalApiUseCases | None = None


_dependencies: PortalDependencies | None = None


def build_dependencies():
    accounts = LinuxUserAccountGateway()
    commands = LinuxCommandRunner()
    inventory = LinuxResourceInventory()
    ollama_client = OllamaClient(commands, OllamaResourcePolicy(settings), settings)
    llm_client = LiteLlmClient(
        settings.HPC_LITELLM_INTERNAL_BASE_URL, settings.HPC_LITELLM_MASTER_KEY
    )
    key_store = OpenWebuiKeyStore(
        settings.OPENWEBUI_LITELLM_KEY_DIR, settings.HPC_PORTAL_PROTECTED_USERS
    )
    llm_gateway = LiteLlmManagementGateway(
        llm_client, ollama_client, key_store, accounts, settings.HPC_OLLAMA_API_BASE
    )
    llm = build_llm_usecases(
        client=llm_client,
        model_inventory=ollama_client,
        key_store=key_store,
        accounts=accounts,
        ollama_base_url=settings.HPC_OLLAMA_API_BASE,
        gateway=llm_gateway,
    )
    job_settings = JobSettings(
        settings.HPC_OPENWEBUI_VERSION,
        settings.HPC_JUPYTER_UBUNTU_VERSION,
        settings.HPC_OLLAMA_DEFAULT_CPUS,
        settings.HPC_OLLAMA_DEFAULT_MEMORY,
    )
    user_settings = UserManagementSettings(
        settings.HPC_PORTAL_GRANT_SUDO,
        frozenset(settings.HPC_PORTAL_PROTECTED_USERS),
        settings.HPC_LITELLM_PUBLIC_BASE_URL,
    )
    jobs = build_jobs_usecases(
        resources=inventory,
        commands=commands,
        user_jobs=HubUserJobs(JupyterHub.instance),
        settings=job_settings,
        policy=ExecutionPolicy(job_settings),
        gateway=llm_gateway,
        ensure_openwebui_key=llm.get_openwebui_key,
    )
    users = build_accounts_usecases(
        accounts=accounts,
        settings=user_settings,
        external_api_factory=get_external_api,
        issue_llm_key=llm.generate_key,
        revoke_llm_access=llm.delete_user_keys,
        set_llm_access=llm.admin_set_api_access,
        stop_openwebui_jobs=jobs.stop_user_openwebui_servers,
        llm_client=llm_client,
        get_llm_access_state=llm.user_external_api_state,
    )
    return PortalDependencies(
        users,
        jobs,
        llm,
        build_ollama_usecases(
            backend=ollama_client,
            register_model=llm.register_ollama_model,
            synchronize_models=llm.sync_ollama_models,
            unregister_model=llm.delete_ollama_model,
            gateway=llm_gateway,
        ),
        ResourceQueryUseCase(inventory, settings.HPC_GPU_COUNT),
    )


def get_dependencies():
    global _dependencies
    if _dependencies is None:
        _dependencies = build_dependencies()
    return _dependencies


def get_external_api():
    config = ExternalApiSettings.from_env()
    if not config.enabled:
        return None
    dependencies = get_dependencies()
    if dependencies.external_api is None:
        config.validate()
        store = EncryptedRecordStore(config.state_dir)
        inventory = LinuxListenerInventory(config)
        accounts = dependencies.users.accounts
        dependencies.external_api = build_external_api_usecases(
            store,
            CloudflareAccessClient(config),
            HubTokenGateway(JupyterHub.instance()),
            config,
            inventory,
            NftablesPortGuard(store, inventory),
            AccessVerifier(config),
            accounts,
            api_relay,
            accounts.linux_users_snapshot,
        )
    return dependencies.external_api


def build_external_api_usecases(
    store,
    cloudflare,
    hub,
    config,
    inventory,
    guard,
    access,
    accounts,
    relay,
    users_snapshot,
):
    repository = ApiAggregateRepository(store)
    queries = ApiRecordQueries(store, accounts, repository, repository)
    health = ApiHealthChecker(guard, inventory, relay)
    return build_api_usecases(
        store=store,
        cloudflare=cloudflare,
        hub_tokens=hub,
        config=config,
        listeners=inventory,
        port_guard=guard,
        access_verifier=access,
        accounts=accounts,
        relay=relay,
        users_snapshot=users_snapshot,
        credential_repository=repository,
        publication_repository=repository,
        queries=queries,
        health=health,
    )
