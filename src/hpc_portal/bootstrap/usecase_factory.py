"""操作間の依存を明示して組み立てる。外部通信は行わない。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from hpc_portal.application.ports.api_query_ports import ApiQueries
from hpc_portal.application.ports.external_api_ports import (
    AccessVerifier,
    ApiConfiguration,
    ApiHealth,
    CloudflareAccess,
    HttpRelay,
    HubTokens,
    ListenerInventory,
    PortGuard,
    RecordStore,
)
from hpc_portal.application.ports.job_management_ports import CommandRunner, UserJobs
from hpc_portal.application.ports.llm_gateway_ports import LlmManagementGateway
from hpc_portal.application.ports.llm_management_ports import (
    KeyStore,
    LlmClient,
    ModelInventory,
)
from hpc_portal.application.ports.ollama_management_ports import OllamaBackend
from hpc_portal.application.ports.resource_monitoring_ports import ResourceInventory
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
from hpc_portal.application.usecase.catalog import (
    AccountUseCases,
    ExternalApiUseCases,
    InferenceUseCases,
    JobUseCases,
    LlmUseCases,
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
from hpc_portal.domain.external_api.repositories import (
    ApiCredentialRepository,
    ApiPublicationRepository,
)
from hpc_portal.domain.jobs.execution_policy import ExecutionPolicy
from hpc_portal.domain.jobs.settings import JobSettings
from hpc_portal.infrastructure.ollama.model_pull_scheduler import ModelPullScheduler


def build_llm_usecases(
    *,
    client: LlmClient,
    gateway: LlmManagementGateway,
    key_store: KeyStore,
    model_inventory: ModelInventory,
    ollama_base_url: str,
    accounts: UserAccountGateway,
) -> LlmUseCases:
    """LLMキー管理とモデル登録の操作を、共有するgatewayへ接続する。"""
    generate_key = IssueLlmKeyUseCase(client=client, gateway=gateway)
    user_external_api_state = GetLlmAccessStateUseCase(gateway=gateway)
    delete_portal_external_keys = DeleteManagedLlmKeysUseCase(
        gateway=gateway, client=client
    )
    set_user_keys_blocked = SetUserLlmKeysBlockedUseCase(gateway=gateway, client=client)
    generate_openwebui_key = IssueOpenWebuiKeyUseCase(client=client, gateway=gateway)
    set_openwebui_key_blocked = SetOpenWebuiKeyBlockedUseCase(
        key_store=key_store, client=client, gateway=gateway
    )
    list_models = ListLlmModelsUseCase(client=client, gateway=gateway)
    register_ollama_model = RegisterLlmModelUseCase(
        client=client,
        gateway=gateway,
        model_inventory=model_inventory,
        ollama_base_url=ollama_base_url,
    )
    delete_ollama_model = UnregisterLlmModelUseCase(client=client, gateway=gateway)
    ensure_external_api_key = EnsureLlmKeyUseCase(
        gateway=gateway, generate_key=generate_key
    )
    regenerate_own_key = RotateLlmKeyUseCase(
        gateway=gateway,
        client=client,
        accounts=accounts,
        delete_portal_external_keys=delete_portal_external_keys,
        generate_key=generate_key,
    )
    delete_user_keys = RevokeLlmAccessUseCase(
        key_store=key_store, client=client, set_user_keys_blocked=set_user_keys_blocked
    )
    get_openwebui_key = EnsureOpenWebuiKeyUseCase(
        client=client,
        gateway=gateway,
        key_store=key_store,
        generate_openwebui_key=generate_openwebui_key,
    )
    sync_ollama_models = SynchronizeLlmModelsUseCase(
        model_inventory=model_inventory, register_ollama_model=register_ollama_model
    )
    admin_set_api_access = SetLlmAccessUseCase(
        client=client,
        accounts=accounts,
        gateway=gateway,
        ensure_external_api_key=ensure_external_api_key,
        set_openwebui_key_blocked=set_openwebui_key_blocked,
        set_user_keys_blocked=set_user_keys_blocked,
    )
    return LlmUseCases(
        client=client,
        gateway=gateway,
        generate_key=generate_key,
        user_external_api_state=user_external_api_state,
        delete_portal_external_keys=delete_portal_external_keys,
        set_user_keys_blocked=set_user_keys_blocked,
        ensure_external_api_key=ensure_external_api_key,
        admin_set_api_access=admin_set_api_access,
        regenerate_own_key=regenerate_own_key,
        delete_user_keys=delete_user_keys,
        generate_openwebui_key=generate_openwebui_key,
        set_openwebui_key_blocked=set_openwebui_key_blocked,
        get_openwebui_key=get_openwebui_key,
        list_models=list_models,
        register_ollama_model=register_ollama_model,
        sync_ollama_models=sync_ollama_models,
        delete_ollama_model=delete_ollama_model,
    )


def build_api_usecases(
    *,
    queries: ApiQueries,
    credential_repository: ApiCredentialRepository,
    cloudflare: CloudflareAccess,
    store: RecordStore,
    hub_tokens: HubTokens,
    publication_repository: ApiPublicationRepository,
    port_guard: PortGuard,
    health: ApiHealth,
    config: ApiConfiguration,
    listeners: ListenerInventory,
    accounts: UserAccountGateway,
    access_verifier: AccessVerifier,
    users_snapshot: Callable[[], list[dict]],
    relay: HttpRelay,
) -> ExternalApiUseCases:
    """認証情報・公開先・同期の操作を組み立て、ロックを共有させる。"""
    sync_lock = asyncio.Lock()
    rotate_credentials = RotateApiCredentialsUseCase(
        queries=queries,
        credential_repository=credential_repository,
        cloudflare=cloudflare,
        store=store,
        hub_tokens=hub_tokens,
    )
    publish_registration = ConfigureApiPublicationUseCase(
        queries=queries,
        publication_repository=publication_repository,
        store=store,
        port_guard=port_guard,
        health=health,
        config=config,
        cloudflare=cloudflare,
        listeners=listeners,
    )
    remove_remote_publication = RemoveRemoteApiPublicationUseCase(
        cloudflare=cloudflare, config=config, store=store, queries=queries
    )
    list_ports = ListApiPortsUseCase(accounts=accounts, listeners=listeners)
    authorize_request = AuthorizeApiInvocationUseCase(
        queries=queries,
        credential_repository=credential_repository,
        publication_repository=publication_repository,
        access_verifier=access_verifier,
        config=config,
        port_guard=port_guard,
    )
    revoke_credential_record = RevokeCredentialRecordUseCase(
        credential_repository=credential_repository,
        hub_tokens=hub_tokens,
        cloudflare=cloudflare,
        store=store,
    )
    issue_credentials = IssueApiCredentialsUseCase(
        queries=queries,
        store=store,
        credential_repository=credential_repository,
        hub_tokens=hub_tokens,
        cloudflare=cloudflare,
        config=config,
        revoke_credential_record=revoke_credential_record,
    )
    revoke_credentials = RevokeApiCredentialsUseCase(
        queries=queries, store=store, revoke_credential_record=revoke_credential_record
    )
    delete_user_records = DeleteUserApiRecordsUseCase(
        queries=queries,
        store=store,
        remove_remote_publication=remove_remote_publication,
    )
    publish_api = RegisterApiPublicationUseCase(
        queries=queries,
        store=store,
        publication_repository=publication_repository,
        accounts=accounts,
        config=config,
        credential_repository=credential_repository,
        listeners=listeners,
        publish_registration=publish_registration,
    )
    operate_publication = ChangeApiPublicationUseCase(
        queries=queries,
        publication_repository=publication_repository,
        credential_repository=credential_repository,
        store=store,
        publish_registration=publish_registration,
        remove_remote_publication=remove_remote_publication,
    )
    refresh_publication = RefreshApiPublicationUseCase(
        queries=queries,
        store=store,
        publication_repository=publication_repository,
        credential_repository=credential_repository,
        listeners=listeners,
        health=health,
        publish_registration=publish_registration,
        remove_remote_publication=remove_remote_publication,
    )
    unpublish_all = UnpublishUserApisUseCase(
        queries=queries,
        store=store,
        publication_repository=publication_repository,
        remove_remote_publication=remove_remote_publication,
    )
    enable_credentials = EnableApiCredentialsUseCase(
        queries=queries,
        store=store,
        credential_repository=credential_repository,
        issue_credentials=issue_credentials,
    )
    disable_user = DisableUserApisUseCase(
        queries=queries,
        store=store,
        credential_repository=credential_repository,
        hub_tokens=hub_tokens,
        revoke_credentials=revoke_credentials,
        unpublish_all=unpublish_all,
    )
    synchronize = SynchronizeUserApisUseCase(
        sync_lock=sync_lock,
        users_snapshot=users_snapshot,
        store=store,
        hub_tokens=hub_tokens,
        port_guard=port_guard,
        disable_user=disable_user,
        issue_credentials=issue_credentials,
        refresh_publication=refresh_publication,
    )
    return ExternalApiUseCases(
        config=config,
        queries=queries,
        accounts=accounts,
        store=store,
        hub_tokens=hub_tokens,
        relay=relay,
        listeners=listeners,
        issue_credentials=issue_credentials,
        rotate_credentials=rotate_credentials,
        revoke_credentials=revoke_credentials,
        enable_credentials=enable_credentials,
        disable_user=disable_user,
        delete_user_records=delete_user_records,
        publish_api=publish_api,
        publish_registration=publish_registration,
        operate_publication=operate_publication,
        remove_remote_publication=remove_remote_publication,
        refresh_publication=refresh_publication,
        unpublish_all=unpublish_all,
        list_ports=list_ports,
        authorize_request=authorize_request,
        synchronize=synchronize,
        revoke_credential_record=revoke_credential_record,
    )


def build_accounts_usecases(
    *,
    settings: UserManagementSettings,
    accounts: UserAccountGateway,
    set_llm_access: SetLlmAccessUseCase,
    stop_openwebui_jobs: StopUserOpenWebuiJobsUseCase,
    llm_client: LlmClient,
    get_llm_access_state: GetLlmAccessStateUseCase,
    external_api_factory: Callable[[], ExternalApiUseCases | None],
    issue_llm_key: IssueLlmKeyUseCase,
    revoke_llm_access: RevokeLlmAccessUseCase,
) -> AccountUseCases:
    """アカウント操作と、関連するAPI権限・ジョブ停止の操作を接続する。"""
    display_name = ChangeAccountDisplayNameUseCase(settings=settings, accounts=accounts)
    password_regenerate = ResetAccountPasswordUseCase(
        settings=settings, accounts=accounts
    )
    sudo = SetAccountSudoUseCase(settings=settings, accounts=accounts)
    api = SetAccountLlmAccessUseCase(
        settings=settings,
        set_llm_access=set_llm_access,
        stop_openwebui_jobs=stop_openwebui_jobs,
    )
    change_password = ChangeAccountPasswordUseCase(accounts=accounts)
    snapshot = ListAccountsUseCase(
        accounts=accounts,
        llm_client=llm_client,
        get_llm_access_state=get_llm_access_state,
        external_api_factory=external_api_factory,
    )
    provision_external_api = ProvisionAccountApiUseCase(
        external_api_factory=external_api_factory
    )
    disable_external_api = DisableAccountApiUseCase(
        external_api_factory=external_api_factory
    )
    enable_external_api = EnableAccountApiUseCase(
        external_api_factory=external_api_factory
    )
    delete_external_api_records = DeleteAccountApiRecordsUseCase(
        external_api_factory=external_api_factory
    )
    create = CreateAccountUseCase(
        settings=settings,
        accounts=accounts,
        issue_llm_key=issue_llm_key,
        provision_external_api=provision_external_api,
    )
    delete = DeleteAccountUseCase(
        settings=settings,
        accounts=accounts,
        revoke_llm_access=revoke_llm_access,
        delete_external_api_records=delete_external_api_records,
        disable_external_api=disable_external_api,
    )
    external_api = SetAccountApiAccessUseCase(
        settings=settings,
        disable_external_api=disable_external_api,
        enable_external_api=enable_external_api,
    )
    return AccountUseCases(
        accounts=accounts,
        settings=settings,
        create=create,
        display_name=display_name,
        delete=delete,
        password_regenerate=password_regenerate,
        sudo=sudo,
        external_api=external_api,
        api=api,
        change_password=change_password,
        snapshot=snapshot,
        provision_external_api=provision_external_api,
        disable_external_api=disable_external_api,
        enable_external_api=enable_external_api,
        delete_external_api_records=delete_external_api_records,
    )


def build_jobs_usecases(
    *,
    policy: ExecutionPolicy,
    resources: ResourceInventory,
    settings: JobSettings,
    user_jobs: UserJobs,
    gateway: LlmManagementGateway,
    commands: CommandRunner,
    ensure_openwebui_key: EnsureOpenWebuiKeyUseCase,
) -> JobUseCases:
    """実行条件の検証、Open WebUIキーの準備、ジョブ停止を組み立てる。"""
    options_from_form = PrepareJobUseCase(
        policy=policy, resources=resources, settings=settings
    )
    stop_user_openwebui_servers = StopUserOpenWebuiJobsUseCase(
        user_jobs=user_jobs, gateway=gateway, commands=commands
    )
    return JobUseCases(
        policy=policy,
        prepare_openwebui=PrepareOpenwebuiLaunchUseCase(
            ensure_openwebui_key=ensure_openwebui_key
        ),
        options_from_form=options_from_form,
        stop_user_openwebui_servers=stop_user_openwebui_servers,
    )


def build_ollama_usecases(
    *,
    backend: OllamaBackend,
    register_model: RegisterLlmModelUseCase,
    synchronize_models: SynchronizeLlmModelsUseCase,
    unregister_model: UnregisterLlmModelUseCase,
    gateway: LlmManagementGateway,
) -> InferenceUseCases:
    """共有推論の操作を組み立て、モデルごとの監視タスクを共有する。"""
    registration_tasks: dict[str, asyncio.Task[None]] = {}
    ollama_register_model = RegisterInstalledModelUseCase(
        backend=backend, register_model=register_model
    )
    ollama_sync_models = SynchronizeInstalledModelsUseCase(
        synchronize_models=synchronize_models
    )
    ollama_delete = DeleteInstalledModelUseCase(
        backend=backend,
        unregister_model=unregister_model,
        register_model=register_model,
    )
    ollama_start = StartInferenceRuntimeUseCase(backend=backend)
    ollama_stop = StopInferenceRuntimeUseCase(backend=backend)
    ollama_update_check = CheckInferenceUpdateUseCase(backend=backend)
    ollama_update = UpdateInferenceRuntimeUseCase(backend=backend)
    ollama_status = GetInferenceRuntimeUseCase(backend=backend)
    ollama_tags = ListInstalledModelsUseCase(backend=backend)
    ollama_pull_cancel = CancelLlmModelPullUseCase(backend=backend)
    ollama_pull_status = GetLlmModelPullStatusUseCase(
        backend=backend, register_model=register_model
    )
    watch_pull_and_register = WatchLlmModelPullUseCase(
        backend=backend,
        gateway=gateway,
        register_model=register_model,
        registration_tasks=registration_tasks,
    )
    pull_scheduler = ModelPullScheduler(
        watch_pull_and_register.execute, registration_tasks
    )
    ollama_pull = PullLlmModelUseCase(
        backend=backend, start_registration_watcher=pull_scheduler.start
    )
    return InferenceUseCases(
        backend=backend,
        ollama_register_model=ollama_register_model,
        ollama_sync_models=ollama_sync_models,
        ollama_delete=ollama_delete,
        ollama_start=ollama_start,
        ollama_stop=ollama_stop,
        ollama_update_check=ollama_update_check,
        ollama_update=ollama_update,
        ollama_status=ollama_status,
        ollama_tags=ollama_tags,
        ollama_pull=ollama_pull,
        ollama_pull_cancel=ollama_pull_cancel,
        ollama_pull_status=ollama_pull_status,
        watch_pull_and_register=watch_pull_and_register,
    )
