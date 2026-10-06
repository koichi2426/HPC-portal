"""アカウントの作成・変更・削除と関連アクセスの調整。"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from hpc_portal.application.ports.llm_management_ports import LlmClient
from hpc_portal.application.ports.user_management_ports import UserAccountGateway
from hpc_portal.domain.accounts.account import Account
from hpc_portal.domain.accounts.account_policy import (
    generate_password,
    validate_display_name,
    validate_password,
    validate_username,
)
from hpc_portal.domain.accounts.settings import UserManagementSettings
from hpc_portal.domain.errors import UseCaseError

if TYPE_CHECKING:
    from hpc_portal.application.usecase.catalog import ExternalApiUseCases
    from hpc_portal.application.usecase.job_management_usecase import (
        StopUserOpenWebuiJobsUseCase,
    )
    from hpc_portal.application.usecase.llm_access_usecase import (
        GetLlmAccessStateUseCase,
        IssueLlmKeyUseCase,
        RevokeLlmAccessUseCase,
        SetLlmAccessUseCase,
    )


HPC_USER_ADMIN_LOG = logging.getLogger("jupyterhub.hpc-user-admin")
HPC_PASSWORD_LOG = logging.getLogger("jupyterhub.hpc-password")


def log_password_success(actor, target):
    HPC_PASSWORD_LOG.info(
        "timestamp=%s actor=%s target=%s",
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        actor,
        target,
    )


def log_user_admin_success(action, actor, target):
    HPC_USER_ADMIN_LOG.info(
        "timestamp=%s action=%s actor=%s target=%s",
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        action,
        actor,
        target,
    )


class CreateAccountUseCase:
    def __init__(
        self,
        *,
        settings: UserManagementSettings,
        accounts: UserAccountGateway,
        issue_llm_key: IssueLlmKeyUseCase,
        provision_external_api: ProvisionAccountApiUseCase,
    ):
        self.settings = settings
        self.accounts = accounts
        self.issue_llm_key = issue_llm_key
        self.provision_external_api = provision_external_api

    async def execute(self, actor, request):
        """入力を検証してLinuxユーザーを作り、外部APIとLLMの認証情報を準備する。"""
        username = request.username
        err = validate_username(username, self.settings.protected_users)
        if err:
            raise UseCaseError(err)
        display_name = request.display_name
        err = validate_display_name(display_name)
        if err:
            raise UseCaseError(err)
        initial_password = generate_password()
        grant_sudo = self.settings.grant_sudo if request.sudo is None else request.sudo
        err = await asyncio.to_thread(
            self.accounts.create_linux_user,
            username,
            initial_password,
            grant_sudo,
            display_name,
        )
        if err:
            raise UseCaseError(err)

        try:
            await self.provision_external_api.execute(username)
        except Exception:
            # Linuxユーザー作成は確定済み。外部APIの発行は定期同期で再試行する。
            HPC_USER_ADMIN_LOG.warning("External API issuance pending for %s", username)
        if grant_sudo:
            log_user_admin_success("sudo_enable", actor, username)
        api_key, key_warning = await asyncio.to_thread(
            self.issue_llm_key.execute, username
        )
        body = {"ok": True, "username": username, "initial_password": initial_password}
        if api_key:
            body["api_key"] = api_key
            body["api_base_url"] = self.settings.llm_public_base_url
        if key_warning:
            body["warning"] = "API key 未発行: " + key_warning
        return body


class ChangeAccountDisplayNameUseCase:
    def __init__(
        self,
        *,
        settings: UserManagementSettings,
        accounts: UserAccountGateway,
    ):
        self.settings = settings
        self.accounts = accounts

    async def execute(self, actor, request):
        """対象アカウントを確認し、Linuxの表示名を更新する。"""
        username = request.username
        account = Account(username, username in self.settings.protected_users)
        account.require_named()
        display_name = request.display_name
        err = await asyncio.to_thread(
            self.accounts.set_linux_display_name, username, display_name
        )
        if err:
            raise UseCaseError(err)
        return {"ok": True, "username": username, "display_name": display_name}


class DeleteAccountUseCase:
    def __init__(
        self,
        *,
        settings: UserManagementSettings,
        accounts: UserAccountGateway,
        revoke_llm_access: RevokeLlmAccessUseCase,
        delete_external_api_records: DeleteAccountApiRecordsUseCase,
        disable_external_api: DisableAccountApiUseCase,
    ):
        self.settings = settings
        self.accounts = accounts
        self.revoke_llm_access = revoke_llm_access
        self.delete_external_api_records = delete_external_api_records
        self.disable_external_api = disable_external_api

    async def execute(self, actor, request):
        """削除可否を確認し、外部APIを失効させてからLinuxユーザーを削除する。"""
        username = request.username
        account = Account(username, username in self.settings.protected_users)
        account.require_deletable_by(actor)

        # 同名ユーザーを再作成しても、以前の接続情報が使われないよう先に失効する。
        try:
            await self.disable_external_api.execute(username)
        except Exception:
            raise UseCaseError(
                "外部 API の失効処理が未完了です。再試行してください", "unavailable"
            )
        err = await asyncio.to_thread(self.accounts.delete_linux_user, username, actor)
        if err:
            raise UseCaseError(err)
        await self.delete_external_api_records.execute(username)
        key_warning = await asyncio.to_thread(self.revoke_llm_access.execute, username)
        body = {"ok": True}
        if key_warning:
            body["warning"] = "LiteLLM key 無効化未確認: " + key_warning
        return body


class ResetAccountPasswordUseCase:
    def __init__(
        self,
        *,
        settings: UserManagementSettings,
        accounts: UserAccountGateway,
    ):
        self.settings = settings
        self.accounts = accounts

    async def execute(self, actor, request):
        """保護対象を確認し、対象ユーザーの初期パスワードを再発行する。"""
        username = request.username
        account = Account(username, username in self.settings.protected_users)
        account.require_password_resettable()
        password = generate_password()
        err = await asyncio.to_thread(
            self.accounts.set_linux_password, username, password
        )
        if err:
            raise UseCaseError(err)
        log_password_success(actor, username)
        return {"ok": True, "username": username, "initial_password": password}


class SetAccountSudoUseCase:
    def __init__(
        self,
        *,
        settings: UserManagementSettings,
        accounts: UserAccountGateway,
    ):
        self.settings = settings
        self.accounts = accounts

    async def execute(self, actor, request):
        """保護対象と管理者自身の権限を確認し、sudo権限を切り替える。"""
        action = request.action
        username = request.username
        account = Account(username, username in self.settings.protected_users)
        enabled = action == "sudo_enable"
        account.require_sudo_changeable_by(actor, enabled)
        err = await asyncio.to_thread(self.accounts.set_linux_sudo, username, enabled)
        if err:
            raise UseCaseError(err)
        log_user_admin_success(action, actor, username)
        return {"ok": True, "username": username, "sudo_enabled": enabled}


class SetAccountApiAccessUseCase:
    def __init__(
        self,
        *,
        settings: UserManagementSettings,
        disable_external_api: DisableAccountApiUseCase,
        enable_external_api: EnableAccountApiUseCase,
    ):
        self.settings = settings
        self.disable_external_api = disable_external_api
        self.enable_external_api = enable_external_api

    async def execute(self, actor, request):
        """保護対象を確認し、ユーザー自身のHTTP APIへの外部アクセスを切り替える。"""
        action = request.action
        username = request.username
        account = Account(username, username in self.settings.protected_users)
        account.require_access_changeable()
        try:
            if action == "external_api_disable":
                await self.disable_external_api.execute(username)
            else:
                await self.enable_external_api.execute(username)
        except Exception:
            raise UseCaseError(
                "外部 API の変更を完了できません。再試行してください", "unavailable"
            )
        return {"ok": True}


class SetAccountLlmAccessUseCase:
    def __init__(
        self,
        *,
        settings: UserManagementSettings,
        set_llm_access: SetLlmAccessUseCase,
        stop_openwebui_jobs: StopUserOpenWebuiJobsUseCase,
    ):
        self.settings = settings
        self.set_llm_access = set_llm_access
        self.stop_openwebui_jobs = stop_openwebui_jobs

    async def execute(self, actor, request):
        """LLM利用権限を切り替え、停止時は利用者のOpen WebUIも終了する。"""
        action = request.action
        username = request.username
        account = Account(username, username in self.settings.protected_users)
        account.require_access_changeable()
        enabled = action == "api_enable"
        api_key, err = await asyncio.to_thread(
            self.set_llm_access.execute, username, enabled
        )
        stop_err = None
        if not enabled:
            stop_err = await self.stop_openwebui_jobs.execute(username)
        errors = [error for error in (err, stop_err) if error]
        if errors:
            raise UseCaseError("; ".join(errors))
        response = {"ok": True, "enabled": enabled}
        if api_key:
            response["api_key"] = api_key
            response["api_base_url"] = self.settings.llm_public_base_url
        return response


class ChangeAccountPasswordUseCase:
    def __init__(
        self,
        *,
        accounts: UserAccountGateway,
    ):
        self.accounts = accounts

    async def execute(
        self, username, current_password, new_password, confirmation, pam_service
    ):
        """現在のパスワードと新しい入力を検証し、本人のパスワードを変更する。"""
        if new_password != confirmation:
            raise UseCaseError("新しいパスワードが確認入力と一致しません")
        error = validate_password(new_password)
        if error:
            raise UseCaseError(error)
        error = await asyncio.to_thread(
            self.accounts.verify_linux_password,
            username,
            current_password,
            service=pam_service,
        )
        if error:
            raise UseCaseError(error)
        error = await asyncio.to_thread(
            self.accounts.set_linux_password, username, new_password
        )
        if error:
            raise UseCaseError(error)
        log_password_success(username, username)
        return {"ok": True}


class ListAccountsUseCase:
    def __init__(
        self,
        *,
        accounts: UserAccountGateway,
        llm_client: LlmClient,
        get_llm_access_state: GetLlmAccessStateUseCase,
        external_api_factory: Callable[[], ExternalApiUseCases | None],
    ):
        self.accounts = accounts
        self.llm_client = llm_client
        self.get_llm_access_state = get_llm_access_state
        self.external_api_factory = external_api_factory

    async def execute(self):
        """Linuxユーザー一覧に、LLM・外部APIの状態とホーム使用量を付ける。"""
        rows = await asyncio.to_thread(self.accounts.linux_users_snapshot)
        # 外部APIとディスク走査は負荷が異なるため、同時実行数を別々に制限する。
        api_semaphore = asyncio.Semaphore(8)
        storage_semaphore = asyncio.Semaphore(4)

        async def enrich(row):
            async with api_semaphore:
                if self.llm_client.enabled():
                    state, error = await asyncio.to_thread(
                        self.get_llm_access_state.execute,
                        row["username"],
                    )
                    message = "LLM APIの状態を取得できません" if error else ""
                else:
                    state, message = ("unknown", "LiteLLM Admin APIが未設定です")
            async with storage_semaphore:
                used, storage_error = await asyncio.to_thread(
                    self.accounts.home_storage_usage, row["home"]
                )

            updated = dict(
                row,
                api_access=state,
                api_access_message=message,
                storage_used_bytes=used,
                storage_message=storage_error or "",
            )
            try:
                external = self.external_api_factory()
                if external:
                    record = external.store.get("credentials", row["username"])
                    updated["external_api_state"] = (record or {}).get(
                        "state", "issuing"
                    )
            except Exception:
                updated["external_api_state"] = "unknown"
            return updated

        return list(await asyncio.gather(*(enrich(row) for row in rows)))


class ProvisionAccountApiUseCase:
    def __init__(
        self,
        *,
        external_api_factory: Callable[[], ExternalApiUseCases | None],
    ):
        self.external_api_factory = external_api_factory

    async def execute(self, username):
        """外部API公開が有効な場合、対象ユーザーの接続情報を準備する。"""
        usecase = self.external_api_factory()
        if usecase:
            await usecase.issue_credentials.execute(
                await usecase.hub_tokens.user(username)
            )


class DisableAccountApiUseCase:
    def __init__(
        self,
        *,
        external_api_factory: Callable[[], ExternalApiUseCases | None],
    ):
        self.external_api_factory = external_api_factory

    async def execute(self, username):
        """外部API公開が有効な場合、対象ユーザーの公開先と認証情報を無効にする。"""
        usecase = self.external_api_factory()
        if usecase:
            await usecase.disable_user.execute(username)


class EnableAccountApiUseCase:
    def __init__(
        self,
        *,
        external_api_factory: Callable[[], ExternalApiUseCases | None],
    ):
        self.external_api_factory = external_api_factory

    async def execute(self, username):
        """外部API公開が有効な場合、失効済みの接続情報を新しく発行する。"""
        usecase = self.external_api_factory()
        if usecase:
            await usecase.enable_credentials.execute(
                await usecase.hub_tokens.user(username)
            )


class DeleteAccountApiRecordsUseCase:
    def __init__(
        self,
        *,
        external_api_factory: Callable[[], ExternalApiUseCases | None],
    ):
        self.external_api_factory = external_api_factory

    async def execute(self, username):
        """外部API公開が有効な場合、失効済みユーザーの登録を削除する。"""
        usecase = self.external_api_factory()
        if usecase:
            await usecase.delete_user_records.execute(username)
