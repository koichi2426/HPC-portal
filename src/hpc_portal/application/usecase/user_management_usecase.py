"""ユーザー作成・削除・権限・パスワード変更の手順。"""

import asyncio
import logging
import time

from hpc_portal.application.ports.user_management_ports import UserAccountGateway
from hpc_portal.domain.errors import UseCaseError
from hpc_portal.domain.user_models import UserManagementSettings
from hpc_portal.domain.user_policy import (
    generate_password,
    validate_display_name,
    validate_password,
    validate_username,
)

HPC_USER_ADMIN_LOG = logging.getLogger("jupyterhub.hpc-user-admin")
HPC_PASSWORD_LOG = logging.getLogger("jupyterhub.hpc-password")


def raise_operation_error(status, message):
    raise UseCaseError(message, "unavailable" if status == 503 else "invalid")


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


class UserManagementUseCase:
    def __init__(
        self,
        accounts: UserAccountGateway,
        llm,
        jobs,
        settings: UserManagementSettings,
        external_api_factory,
    ):
        self.accounts = accounts
        self.llm = llm
        self.jobs = jobs
        self.settings = settings
        self.external_api_factory = external_api_factory

    async def execute(self, actor, request):
        action = request.action
        username = request.username
        if action == "create":
            err = validate_username(username, self.settings.protected_users)
            if err:
                raise_operation_error(400, err)
            display_name = request.display_name
            err = validate_display_name(display_name)
            if err:
                raise_operation_error(400, err)
            initial_password = generate_password()
            grant_sudo = (
                self.settings.grant_sudo if request.sudo is None else request.sudo
            )
            err = await asyncio.to_thread(
                self.accounts.create_linux_user,
                username,
                initial_password,
                grant_sudo,
                display_name,
            )
            if err:
                raise_operation_error(400, err)
            try:
                await self.provision_external_api(username)
            except Exception:
                HPC_USER_ADMIN_LOG.warning(
                    "External API issuance pending for %s", username
                )
            if grant_sudo:
                log_user_admin_success("sudo_enable", actor, username)
            api_key, key_warning = await asyncio.to_thread(
                self.llm.generate_key, username
            )
            body = {
                "ok": True,
                "username": username,
                "initial_password": initial_password,
            }
            if api_key:
                body["api_key"] = api_key
                body["api_base_url"] = self.settings.llm_public_base_url
            if key_warning:
                body["warning"] = "API key 未発行: " + key_warning
            return body
        if action == "display_name":
            if not username:
                raise_operation_error(400, "username が必要です")
            display_name = request.display_name
            err = await asyncio.to_thread(
                self.accounts.set_linux_display_name, username, display_name
            )
            if err:
                raise_operation_error(400, err)
            return {"ok": True, "username": username, "display_name": display_name}
        if action == "delete":
            if not username:
                raise_operation_error(400, "username が必要です")
            if username in self.settings.protected_users or username == actor:
                raise_operation_error(400, "保護されたユーザーは削除できません")
            try:
                await self.disable_external_api(username)
            except Exception:
                raise_operation_error(
                    503, "外部 API の失効処理が未完了です。再試行してください"
                )
            err = await asyncio.to_thread(
                self.accounts.delete_linux_user, username, actor
            )
            if err:
                raise_operation_error(400, err)
            await self.delete_external_api_records(username)
            key_warning = await asyncio.to_thread(self.llm.delete_user_keys, username)
            body = {"ok": True}
            if key_warning:
                body["warning"] = "LiteLLM key 無効化未確認: " + key_warning
            return body
        if action == "password_regenerate":
            if not username:
                raise_operation_error(400, "username が必要です")
            if username in self.settings.protected_users:
                raise_operation_error(400, "保護されたユーザーは再発行できません")
            password = generate_password()
            err = await asyncio.to_thread(
                self.accounts.set_linux_password, username, password
            )
            if err:
                raise_operation_error(400, err)
            log_password_success(actor, username)
            return {"ok": True, "username": username, "initial_password": password}
        if action in {"sudo_enable", "sudo_disable"}:
            if not username:
                raise_operation_error(400, "username が必要です")
            enabled = action == "sudo_enable"
            if not enabled and username in self.settings.protected_users:
                raise_operation_error(
                    400, "保護されたユーザーのsudo権限は解除できません"
                )
            if not enabled and username == actor:
                raise_operation_error(
                    400, "ログイン中の自分自身のsudo権限は解除できません"
                )
            err = await asyncio.to_thread(
                self.accounts.set_linux_sudo, username, enabled
            )
            if err:
                raise_operation_error(400, err)
            log_user_admin_success(action, actor, username)
            return {"ok": True, "username": username, "sudo_enabled": enabled}
        if action in {"external_api_disable", "external_api_enable"}:
            if not username or username in self.settings.protected_users:
                raise_operation_error(400, "対象ユーザーを確認してください")
            try:
                if action == "external_api_disable":
                    await self.disable_external_api(username)
                else:
                    await self.enable_external_api(username)
            except Exception:
                raise_operation_error(
                    503, "外部 API の変更を完了できません。再試行してください"
                )
            return {"ok": True}
        if action in {"api_disable", "api_enable"}:
            if not username:
                raise_operation_error(400, "username が必要です")
            if username in self.settings.protected_users:
                raise_operation_error(
                    400, "保護されたユーザーのLLM APIは変更できません"
                )
            enabled = action == "api_enable"
            api_key, err = await asyncio.to_thread(
                self.llm.admin_set_api_access, username, enabled
            )
            stop_err = None
            if not enabled:
                # key blockが一部失敗しても、起動中Open WebUIの停止は必ず試行する。
                stop_err = await self.jobs.stop_user_openwebui_servers(username)
            errors = [error for error in (err, stop_err) if error]
            if errors:
                raise_operation_error(400, "; ".join(errors))
            response = {"ok": True, "enabled": enabled}
            if api_key:
                response["api_key"] = api_key
                response["api_base_url"] = self.settings.llm_public_base_url
            return response
        raise UseCaseError("不明な action です")

    async def provision_external_api(self, username):
        usecase = self.external_api_factory()
        if usecase:
            await usecase.issue_credentials(await usecase.hub.user(username))

    async def disable_external_api(self, username):
        usecase = self.external_api_factory()
        if usecase:
            await usecase.disable_user(username)

    async def enable_external_api(self, username):
        usecase = self.external_api_factory()
        if usecase:
            await usecase.enable_credentials(await usecase.hub.user(username))

    async def delete_external_api_records(self, username):
        usecase = self.external_api_factory()
        if usecase:
            await usecase.delete_user_records(username)

    async def change_password(
        self, username, current_password, new_password, confirmation, pam_service
    ):
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

    async def snapshot(self):
        rows = await asyncio.to_thread(self.accounts.linux_users_snapshot)
        api_semaphore = asyncio.Semaphore(8)
        storage_semaphore = asyncio.Semaphore(4)

        async def enrich(row):
            async with api_semaphore:
                if self.llm.client.enabled():
                    state, error = await asyncio.to_thread(
                        self.llm.user_external_api_state, row["username"]
                    )
                    message = "LLM APIの状態を取得できません" if error else ""
                else:
                    state, message = "unknown", "LiteLLM Admin APIが未設定です"
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
