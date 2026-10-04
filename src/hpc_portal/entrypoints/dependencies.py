"""外部接続の実装を組み立て、各usecaseへ渡す。"""

from dataclasses import dataclass, field

from jupyterhub.app import JupyterHub

from hpc_portal.application.usecase.external_api_usecase import ExternalApiUseCase
from hpc_portal.application.usecase.job_management_usecase import JobManagementUseCase
from hpc_portal.application.usecase.llm_management_usecase import LlmManagementUseCase
from hpc_portal.application.usecase.ollama_management_usecase import (
    OllamaManagementUseCase,
)
from hpc_portal.application.usecase.resource_monitoring_usecase import (
    ResourceMonitoringUseCase,
)
from hpc_portal.application.usecase.user_management_usecase import UserManagementUseCase
from hpc_portal.domain.job_models import JobSettings
from hpc_portal.domain.resource_policy import OllamaResourcePolicy
from hpc_portal.domain.user_models import UserManagementSettings
from hpc_portal.infrastructure.cloudflare.access_client import CloudflareAccessClient
from hpc_portal.infrastructure.cloudflare.access_token_verifier import AccessVerifier
from hpc_portal.infrastructure.config import settings
from hpc_portal.infrastructure.config.external_api_settings import ExternalApiSettings
from hpc_portal.infrastructure.http import api_relay
from hpc_portal.infrastructure.jupyterhub.token_gateway import HubTokenGateway
from hpc_portal.infrastructure.jupyterhub.user_jobs import HubUserJobs
from hpc_portal.infrastructure.linux.command_runner import LinuxCommandRunner
from hpc_portal.infrastructure.linux.listener_inventory import LinuxListenerInventory
from hpc_portal.infrastructure.linux.port_guard import NftablesPortGuard
from hpc_portal.infrastructure.linux.resource_inventory import LinuxResourceInventory
from hpc_portal.infrastructure.linux.user_account_gateway import LinuxUserAccountGateway
from hpc_portal.infrastructure.litellm.api_client import LiteLlmClient
from hpc_portal.infrastructure.ollama.ollama_client import OllamaClient
from hpc_portal.infrastructure.openwebui.api_key_store import OpenWebuiKeyStore
from hpc_portal.infrastructure.persistence.encrypted_record_store import (
    EncryptedRecordStore,
)


@dataclass
class PortalDependencies:
    users: UserManagementUseCase
    jobs: JobManagementUseCase
    llm: LlmManagementUseCase
    ollama: OllamaManagementUseCase
    resources: ResourceMonitoringUseCase
    openwebui_key_locks: dict = field(default_factory=dict)
    external_api: ExternalApiUseCase | None = None


_dependencies: PortalDependencies | None = None


def build_dependencies():
    accounts = LinuxUserAccountGateway()
    commands = LinuxCommandRunner()
    inventory = LinuxResourceInventory()
    ollama_client = OllamaClient(commands, OllamaResourcePolicy(settings), settings)
    llm = LlmManagementUseCase(
        LiteLlmClient(
            settings.HPC_LITELLM_INTERNAL_BASE_URL, settings.HPC_LITELLM_MASTER_KEY
        ),
        ollama_client,
        OpenWebuiKeyStore(
            settings.OPENWEBUI_LITELLM_KEY_DIR, settings.HPC_PORTAL_PROTECTED_USERS
        ),
        accounts,
        settings.HPC_OLLAMA_API_BASE,
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
    jobs = JobManagementUseCase(
        inventory, commands, HubUserJobs(JupyterHub.instance), job_settings, llm
    )
    users = UserManagementUseCase(accounts, llm, jobs, user_settings, get_external_api)
    return PortalDependencies(
        users,
        jobs,
        llm,
        OllamaManagementUseCase(ollama_client, llm),
        ResourceMonitoringUseCase(inventory, settings.HPC_GPU_COUNT),
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
        dependencies.external_api = ExternalApiUseCase(
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
