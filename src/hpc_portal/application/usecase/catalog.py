"""呼び出し元が使う操作と参照先。usecaseの依存注入には使わない。"""

from __future__ import annotations

from dataclasses import dataclass

from hpc_portal.application.ports.api_query_ports import ApiQueries
from hpc_portal.application.ports.external_api_ports import (
    ApiConfiguration,
    HttpRelay,
    HubTokens,
    ListenerInventory,
    RecordStore,
)
from hpc_portal.application.ports.llm_gateway_ports import LlmManagementGateway
from hpc_portal.application.ports.llm_management_ports import LlmClient
from hpc_portal.application.ports.ollama_management_ports import OllamaBackend
from hpc_portal.application.ports.user_management_ports import UserAccountGateway
from hpc_portal.application.usecase.account_management_usecase import (
    ChangeAccountDisplayNameUseCase,
    ChangeAccountPasswordUseCase,
    CreateAccountUseCase,
    DeleteAccountApiRecordsUseCase,
    DeleteAccountUseCase,
    DisableAccountApiUseCase,
    EnableAccountApiUseCase,
    ListAccountsUseCase,
    ProvisionAccountApiUseCase,
    ResetAccountPasswordUseCase,
    SetAccountApiAccessUseCase,
    SetAccountLlmAccessUseCase,
    SetAccountSshAccessUseCase,
    SetAccountSudoUseCase,
)
from hpc_portal.application.usecase.api_credentials_usecase import (
    DeleteUserApiRecordsUseCase,
    DisableUserApisUseCase,
    EnableApiCredentialsUseCase,
    IssueApiCredentialsUseCase,
    RevokeApiCredentialsUseCase,
    RevokeCredentialRecordUseCase,
    RotateApiCredentialsUseCase,
)
from hpc_portal.application.usecase.api_invocation_usecase import (
    AuthorizeApiInvocationUseCase,
)
from hpc_portal.application.usecase.api_publication_usecase import (
    ChangeApiPublicationUseCase,
    ConfigureApiPublicationUseCase,
    ListApiPortsUseCase,
    RefreshApiPublicationUseCase,
    RegisterApiPublicationUseCase,
    RemoveRemoteApiPublicationUseCase,
    UnpublishUserApisUseCase,
)
from hpc_portal.application.usecase.api_synchronization_usecase import (
    SynchronizeUserApisUseCase,
)
from hpc_portal.application.usecase.hub_credentials_usecase import (
    ManageHubCredentialsUseCase,
)
from hpc_portal.application.usecase.inference_runtime_usecase import (
    CheckInferenceUpdateUseCase,
    GetInferenceRuntimeUseCase,
    ListInstalledModelsUseCase,
    StartInferenceRuntimeUseCase,
    StopInferenceRuntimeUseCase,
    UpdateInferenceRuntimeUseCase,
)
from hpc_portal.application.usecase.job_management_usecase import (
    PrepareJobUseCase,
    PrepareOpenwebuiLaunchUseCase,
    StopUserOpenWebuiJobsUseCase,
)
from hpc_portal.application.usecase.llm_access_usecase import (
    DeleteManagedLlmKeysUseCase,
    EnsureLlmKeyUseCase,
    EnsureOpenWebuiKeyUseCase,
    GetLlmAccessStateUseCase,
    IssueLlmKeyUseCase,
    IssueOpenWebuiKeyUseCase,
    RevokeLlmAccessUseCase,
    RotateLlmKeyUseCase,
    SetLlmAccessUseCase,
    SetOpenWebuiKeyBlockedUseCase,
    SetUserLlmKeysBlockedUseCase,
)
from hpc_portal.application.usecase.llm_model_management_usecase import (
    CancelLlmModelPullUseCase,
    DeleteInstalledModelUseCase,
    GetLlmModelPullStatusUseCase,
    ListLlmModelsUseCase,
    PullLlmModelUseCase,
    RegisterInstalledModelUseCase,
    RegisterLlmModelUseCase,
    SynchronizeInstalledModelsUseCase,
    SynchronizeLlmModelsUseCase,
    UnregisterLlmModelUseCase,
    WatchLlmModelPullUseCase,
)
from hpc_portal.domain.accounts.settings import UserManagementSettings
from hpc_portal.domain.jobs.execution_policy import ExecutionPolicy


@dataclass
class LlmUseCases:
    client: LlmClient
    gateway: LlmManagementGateway
    generate_key: IssueLlmKeyUseCase
    user_external_api_state: GetLlmAccessStateUseCase
    delete_portal_external_keys: DeleteManagedLlmKeysUseCase
    set_user_keys_blocked: SetUserLlmKeysBlockedUseCase
    ensure_external_api_key: EnsureLlmKeyUseCase
    admin_set_api_access: SetLlmAccessUseCase
    regenerate_own_key: RotateLlmKeyUseCase
    delete_user_keys: RevokeLlmAccessUseCase
    generate_openwebui_key: IssueOpenWebuiKeyUseCase
    set_openwebui_key_blocked: SetOpenWebuiKeyBlockedUseCase
    get_openwebui_key: EnsureOpenWebuiKeyUseCase
    list_models: ListLlmModelsUseCase
    register_ollama_model: RegisterLlmModelUseCase
    sync_ollama_models: SynchronizeLlmModelsUseCase
    delete_ollama_model: UnregisterLlmModelUseCase


@dataclass
class ExternalApiUseCases:
    config: ApiConfiguration
    queries: ApiQueries
    accounts: UserAccountGateway
    store: RecordStore
    hub_tokens: HubTokens
    relay: HttpRelay
    listeners: ListenerInventory
    issue_credentials: IssueApiCredentialsUseCase
    hub_credentials: ManageHubCredentialsUseCase
    rotate_credentials: RotateApiCredentialsUseCase
    revoke_credentials: RevokeApiCredentialsUseCase
    enable_credentials: EnableApiCredentialsUseCase
    disable_user: DisableUserApisUseCase
    delete_user_records: DeleteUserApiRecordsUseCase
    publish_api: RegisterApiPublicationUseCase
    publish_registration: ConfigureApiPublicationUseCase
    operate_publication: ChangeApiPublicationUseCase
    remove_remote_publication: RemoveRemoteApiPublicationUseCase
    refresh_publication: RefreshApiPublicationUseCase
    unpublish_all: UnpublishUserApisUseCase
    list_ports: ListApiPortsUseCase
    authorize_request: AuthorizeApiInvocationUseCase
    synchronize: SynchronizeUserApisUseCase
    revoke_credential_record: RevokeCredentialRecordUseCase


@dataclass
class AccountUseCases:
    accounts: UserAccountGateway
    settings: UserManagementSettings
    create: CreateAccountUseCase
    display_name: ChangeAccountDisplayNameUseCase
    delete: DeleteAccountUseCase
    password_regenerate: ResetAccountPasswordUseCase
    sudo: SetAccountSudoUseCase
    external_api: SetAccountApiAccessUseCase
    ssh_access: SetAccountSshAccessUseCase
    api: SetAccountLlmAccessUseCase
    change_password: ChangeAccountPasswordUseCase
    snapshot: ListAccountsUseCase
    provision_external_api: ProvisionAccountApiUseCase
    disable_external_api: DisableAccountApiUseCase
    enable_external_api: EnableAccountApiUseCase
    delete_external_api_records: DeleteAccountApiRecordsUseCase


@dataclass
class JobUseCases:
    policy: ExecutionPolicy
    prepare_openwebui: PrepareOpenwebuiLaunchUseCase
    options_from_form: PrepareJobUseCase
    stop_user_openwebui_servers: StopUserOpenWebuiJobsUseCase


@dataclass
class InferenceUseCases:
    backend: OllamaBackend
    ollama_register_model: RegisterInstalledModelUseCase
    ollama_sync_models: SynchronizeInstalledModelsUseCase
    ollama_delete: DeleteInstalledModelUseCase
    ollama_start: StartInferenceRuntimeUseCase
    ollama_stop: StopInferenceRuntimeUseCase
    ollama_update_check: CheckInferenceUpdateUseCase
    ollama_update: UpdateInferenceRuntimeUseCase
    ollama_status: GetInferenceRuntimeUseCase
    ollama_tags: ListInstalledModelsUseCase
    ollama_pull: PullLlmModelUseCase
    ollama_pull_cancel: CancelLlmModelPullUseCase
    ollama_pull_status: GetLlmModelPullStatusUseCase
    watch_pull_and_register: WatchLlmModelPullUseCase
