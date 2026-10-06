"""LLM利用権限の操作手順。"""

from __future__ import annotations

from hpc_portal.application.ports.llm_gateway_ports import LlmManagementGateway
from hpc_portal.application.ports.llm_management_ports import KeyStore, LlmClient
from hpc_portal.application.ports.user_management_ports import UserAccountGateway
from hpc_portal.domain.llm.access import LlmAccess


class IssueLlmKeyUseCase:
    def __init__(
        self,
        *,
        client: LlmClient,
        gateway: LlmManagementGateway,
    ):
        self.client = client
        self.gateway = gateway

    def execute(self, username: str) -> tuple[str | None, str | None]:
        """外部からLLMを呼び出すためのVirtual Keyを発行する。

        Args:
            username: Keyを所有するLinuxユーザー名。

        Returns:
            ``(平文Key, エラー)``。成功時のエラーはNone。
        """
        if not self.client.enabled():
            return (None, "LiteLLM Admin API が未設定のため API key は未発行です")
        user_err = self.gateway.ensure_user(username)
        if user_err:
            return (None, user_err)
        payload = {
            "user_id": username,
            "key_alias": username,
            "metadata": {
                "linux_username": username,
                "source": "hpc-portal",
                "admin_disabled": False,
            },
        }
        try:
            data = self.client.request("/key/generate", payload)
        except RuntimeError as exc:
            return (None, str(exc))
        key = data.get("key") or data.get("token")
        if not key:
            return (None, "LiteLLM key 発行レスポンスに key が含まれていません")
        return (key, None)


class GetLlmAccessStateUseCase:
    def __init__(
        self,
        *,
        gateway: LlmManagementGateway,
    ):
        self.gateway = gateway

    def execute(self, username: str) -> tuple[str, str | None]:
        """管理画面向けにLLM APIの利用状態を取得する。

        Args:
            username: 状態を取得するLinuxユーザー名。

        Returns:
            ``(状態, エラー)``。状態は enabled / disabled / unissued / unknown。
        """
        disabled, err = self.gateway.user_admin_disabled(username)
        if err:
            lowered = str(err).lower()
            if "http 404" in lowered or "not found" in lowered:
                return ("unissued", None)
            return ("unknown", self.gateway.safe_litellm_error(err))
        if disabled:
            return ("disabled", None)
        records, err = self.gateway.list_user_keys(username)
        if err:
            return ("unknown", self.gateway.safe_litellm_error(err))
        if any(
            (
                self.gateway.is_portal_external_key(record, username)
                for record in records
            )
        ):
            return ("enabled", None)
        return ("unissued", None)


class DeleteManagedLlmKeysUseCase:
    def __init__(
        self,
        *,
        gateway: LlmManagementGateway,
        client: LlmClient,
    ):
        self.gateway = gateway
        self.client = client

    def execute(self, username: str) -> str | None:
        """再発行前にポータル発行の外部API keyをblockして削除・確認する。

        Open WebUI用（source=hpc-portal-openwebui）のkeyには一切触れない。

        Args:
            username: 対象のLinuxユーザー名。

        Returns:
            正常ならNone、失敗時はエラーメッセージ。
        """
        records, err = self.gateway.list_user_keys(username)
        if err:
            return err
        targets = [
            record
            for record in records
            if self.gateway.is_portal_external_key(record, username)
        ]
        if not targets:
            return None
        key_ids = []
        for record in targets:
            key_id = self.gateway.key_identifier(record)
            if key_id:
                key_ids.append(key_id)
        for key_id in key_ids:
            try:
                self.client.request("/key/block", {"key": key_id})
            except RuntimeError as exc:
                safe_error = self.gateway.safe_litellm_error(exc)
                self.gateway.log_litellm_action(
                    "api_key_regenerate_block", username, "failed", safe_error
                )
                return safe_error
        try:
            self.client.request("/key/delete", {"key_aliases": [username]})
        except RuntimeError as exc:
            safe_error = self.gateway.safe_litellm_error(exc)
            self.gateway.log_litellm_action(
                "api_key_regenerate_delete", username, "failed", safe_error
            )
            return safe_error
        remaining, list_err = self.gateway.list_user_keys(username)
        if list_err:
            safe_error = self.gateway.safe_litellm_error(list_err)
            self.gateway.log_litellm_action(
                "api_key_regenerate_verify", username, "failed", safe_error
            )
            return safe_error
        if any(
            (
                self.gateway.is_portal_external_key(record, username)
                for record in remaining
            )
        ):
            message = "古い外部API keyの削除を確認できません"
            self.gateway.log_litellm_action(
                "api_key_regenerate_verify", username, "failed", message
            )
            return message
        self.gateway.log_litellm_action("api_key_regenerate_delete", username, "ok")
        return None


class SetUserLlmKeysBlockedUseCase:
    def __init__(
        self,
        *,
        gateway: LlmManagementGateway,
        client: LlmClient,
    ):
        self.gateway = gateway
        self.client = client

    def execute(
        self,
        username: str,
        blocked: bool,
        *,
        mark_admin_disabled: bool | None = None,
        include_openwebui: bool = True,
    ) -> str | None:
        """利用者に属するVirtual Keyを一括でblockまたはunblockする。

        Args:
            username: Linuxユーザー名。
            blocked: blockする場合はTrue。
            mark_admin_disabled: Key metadataへ記録する停止状態。
            include_openwebui: Open WebUI専用Keyも対象にするか。

        Returns:
            正常ならNone、一部でも失敗した場合はエラーメッセージ。
        """
        records, err = self.gateway.list_user_keys(username)
        if err:
            return err
        endpoint = "/key/block" if blocked else "/key/unblock"
        action = "user_keys_block" if blocked else "user_keys_unblock"
        errors = []
        for record in records:
            metadata = self.gateway.metadata(record.get("metadata"))
            if (
                not include_openwebui
                and metadata.get("source") == "hpc-portal-openwebui"
            ):
                continue
            key_id = self.gateway.key_identifier(record)
            if not key_id:
                errors.append("LiteLLM key の識別子が取得できません")
                continue
            try:
                self.client.request(endpoint, {"key": key_id})
            except RuntimeError as exc:
                errors.append(str(exc))
                continue
            if mark_admin_disabled is not None:
                meta_err = self.gateway.set_key_metadata(record, mark_admin_disabled)
                if meta_err:
                    pass
        if errors:
            joined = "; ".join(
                (self.gateway.safe_litellm_error(error) for error in errors)
            )
            self.gateway.log_litellm_action(action, username, "failed", joined)
            return joined
        self.gateway.log_litellm_action(action, username, "ok")
        return None


class EnsureLlmKeyUseCase:
    def __init__(
        self,
        *,
        gateway: LlmManagementGateway,
        generate_key: IssueLlmKeyUseCase,
    ):
        self.gateway = gateway
        self.generate_key = generate_key

    def execute(self, username: str) -> tuple[str | None, str | None]:
        """ポータル用外部API keyがなければ新規発行する。

        Args:
            username: keyを所有するLinuxユーザー名。

        Returns:
            新規発行した平文keyとエラーメッセージの組。既存keyがある場合は
            どちらもNoneを返す。
        """
        with self.gateway.external_api_key_lock(username):
            records, err = self.gateway.list_user_keys(username)
            if err:
                return (None, err)
            if any(
                (
                    self.gateway.is_portal_external_key(record, username)
                    for record in records
                )
            ):
                return (None, None)
            return self.generate_key.execute(username)


class SetLlmAccessUseCase:
    def __init__(
        self,
        *,
        client: LlmClient,
        accounts: UserAccountGateway,
        gateway: LlmManagementGateway,
        ensure_external_api_key: EnsureLlmKeyUseCase,
        set_openwebui_key_blocked: SetOpenWebuiKeyBlockedUseCase,
        set_user_keys_blocked: SetUserLlmKeysBlockedUseCase,
    ):
        self.client = client
        self.accounts = accounts
        self.gateway = gateway
        self.ensure_external_api_key = ensure_external_api_key
        self.set_openwebui_key_blocked = set_openwebui_key_blocked
        self.set_user_keys_blocked = set_user_keys_blocked

    def execute(self, username: str, enabled: bool) -> tuple[str | None, str | None]:
        """利用者単位でLLM APIとOpen WebUIの利用可否を切り替える。

        Args:
            username: 対象のLinuxユーザー名。
            enabled: 有効化する場合はTrue。

        Returns:
            ``(新規発行したAPI key, エラー)``。既存keyの再有効化時はkeyを返さない。
        """
        if not self.client.enabled():
            return (None, "LiteLLM Admin API が未設定です")
        try:
            self.accounts.getpwnam(username)
        except KeyError:
            return (None, "ユーザーが見つかりません")
        if not enabled:
            user_err = self.gateway.set_user_admin_disabled(username, True)
            if user_err:
                self.gateway.log_litellm_action(
                    "api_disable", username, "failed", user_err
                )
                return (None, user_err)
            external_err = self.set_user_keys_blocked.execute(
                username,
                blocked=True,
                mark_admin_disabled=True,
                include_openwebui=False,
            )
            openwebui_err = self.set_openwebui_key_blocked.execute(username, True)
            errors = [error for error in (external_err, openwebui_err) if error]
            if errors:
                joined = "; ".join(errors)
                self.gateway.log_litellm_action(
                    "api_disable", username, "partial", joined
                )
                return (None, joined)
            self.gateway.log_litellm_action("api_disable", username, "ok")
            return (None, None)
        user_err = self.gateway.ensure_user(username)
        if user_err:
            self.gateway.log_litellm_action("api_enable", username, "failed", user_err)
            return (None, user_err)
        external_err = self.set_user_keys_blocked.execute(
            username, blocked=False, mark_admin_disabled=False, include_openwebui=False
        )
        openwebui_err = self.set_openwebui_key_blocked.execute(username, False)
        errors = [error for error in (external_err, openwebui_err) if error]
        if errors:
            # 一部だけ利用可能になるのを避け、失敗時は両用途のキーを停止へ戻す。
            joined = "; ".join(errors)
            self.gateway.set_user_admin_disabled(username, True)
            self.set_user_keys_blocked.execute(
                username,
                blocked=True,
                mark_admin_disabled=True,
                include_openwebui=False,
            )
            self.set_openwebui_key_blocked.execute(username, True)
            self.gateway.log_litellm_action("api_enable", username, "failed", joined)
            return (None, joined)
        user_err = self.gateway.set_user_admin_disabled(username, False)
        if user_err:
            self.set_user_keys_blocked.execute(
                username,
                blocked=True,
                mark_admin_disabled=True,
                include_openwebui=False,
            )
            self.set_openwebui_key_blocked.execute(username, True)
            self.gateway.log_litellm_action("api_enable", username, "failed", user_err)
            return (None, user_err)
        api_key, key_err = self.ensure_external_api_key.execute(username)
        if key_err:
            self.gateway.set_user_admin_disabled(username, True)
            self.set_user_keys_blocked.execute(
                username,
                blocked=True,
                mark_admin_disabled=True,
                include_openwebui=False,
            )
            self.set_openwebui_key_blocked.execute(username, True)
            safe_error = self.gateway.safe_litellm_error(key_err)
            self.gateway.log_litellm_action(
                "api_enable_key_generate", username, "failed", safe_error
            )
            return (None, safe_error)
        if api_key:
            self.gateway.log_litellm_action("api_enable_key_generate", username, "ok")
        self.gateway.log_litellm_action("api_enable", username, "ok")
        return (api_key, None)


class RotateLlmKeyUseCase:
    def __init__(
        self,
        *,
        gateway: LlmManagementGateway,
        client: LlmClient,
        accounts: UserAccountGateway,
        delete_portal_external_keys: DeleteManagedLlmKeysUseCase,
        generate_key: IssueLlmKeyUseCase,
    ):
        self.gateway = gateway
        self.client = client
        self.accounts = accounts
        self.delete_portal_external_keys = delete_portal_external_keys
        self.generate_key = generate_key

    def execute(self, username: str) -> tuple[str | None, str | None]:
        """外部API用Keyを安全に削除して同じaliasで再発行する。

        Args:
            username: 再発行する本人のLinuxユーザー名。

        Returns:
            ``(新しい平文Key, エラー)``。Open WebUI Keyは変更しない。
        """
        with self.gateway.external_api_key_lock(username):
            if not self.client.enabled():
                return (None, "LiteLLM Admin API が未設定です")
            try:
                self.accounts.getpwnam(username)
            except KeyError:
                return (None, "ユーザーが見つかりません")
            disabled, err = self.gateway.user_admin_disabled(username)
            if err:
                return (None, err)
            issuance_error = LlmAccess(username, disabled).issuance_error(
                "API key は管理者により無効化されています"
            )
            if issuance_error:
                return (None, issuance_error)
            err = self.delete_portal_external_keys.execute(username)
            if err:
                return (None, err)
            key, err = self.generate_key.execute(username)
            if err:
                self.gateway.log_litellm_action(
                    "api_key_regenerate_generate", username, "failed", err
                )
                return (None, err)
            self.gateway.log_litellm_action(
                "api_key_regenerate_generate", username, "ok"
            )
            return (key, None)


class RevokeLlmAccessUseCase:
    def __init__(
        self,
        *,
        key_store: KeyStore,
        client: LlmClient,
        set_user_keys_blocked: SetUserLlmKeysBlockedUseCase,
    ):
        self.key_store = key_store
        self.client = client
        self.set_user_keys_blocked = set_user_keys_blocked

    def execute(self, username: str) -> str | None:
        """ユーザー削除時に関連Virtual Keyと保存ファイルを破棄する。

        Args:
            username: 削除対象のLinuxユーザー名。

        Returns:
            正常ならNone、無効化または削除失敗時はエラーメッセージ。
        """
        self.key_store.remove(username)
        if not self.client.enabled():
            return "LiteLLM Admin API が未設定のため key 無効化は未確認です"
        block_err = self.set_user_keys_blocked.execute(
            username, blocked=True, mark_admin_disabled=True
        )
        payload_candidates = ({"user_ids": [username]}, {"user_id": username})
        last_err = None
        for payload in payload_candidates:
            try:
                self.client.request("/user/delete", payload)
                return block_err
            except RuntimeError as exc:
                last_err = str(exc)
        return block_err or last_err or "LiteLLM user/key 無効化に失敗しました"


class IssueOpenWebuiKeyUseCase:
    def __init__(
        self,
        *,
        client: LlmClient,
        gateway: LlmManagementGateway,
    ):
        self.client = client
        self.gateway = gateway

    def execute(self, username: str) -> tuple[str | None, str | None]:
        """ユーザーごとに再利用する Open WebUI 専用 Virtual Key を初回だけ発行する。

        Args:
            username: 対象のLinuxユーザー名。

        Returns:
            発行したKeyとエラーメッセージの組。
        """
        if not self.client.enabled():
            return (None, "LiteLLM Admin API が未設定です")
        user_err = self.gateway.ensure_user(username)
        if user_err:
            return (None, user_err)
        disabled, err = self.gateway.user_admin_disabled(username)
        if err:
            return (None, err)
        issuance_error = LlmAccess(username, disabled).issuance_error(
            "API 利用が管理者により停止されています"
        )
        if issuance_error:
            return (None, issuance_error)
        payload = {
            "user_id": username,
            "key_alias": f"openwebui-{username}",
            "metadata": {
                "linux_username": username,
                "source": "hpc-portal-openwebui",
                "admin_disabled": False,
            },
        }
        try:
            data = self.client.request("/key/generate", payload)
        except RuntimeError as exc:
            return (None, str(exc))
        key = data.get("key") or data.get("token")
        if not key:
            return (None, "Open WebUI 用 key 発行レスポンスに key が含まれていません")
        self.gateway.log_litellm_action("openwebui_key_generate", username, "ok")
        return (key, None)


class SetOpenWebuiKeyBlockedUseCase:
    def __init__(
        self,
        *,
        key_store: KeyStore,
        client: LlmClient,
        gateway: LlmManagementGateway,
    ):
        self.key_store = key_store
        self.client = client
        self.gateway = gateway

    def execute(self, username: str, blocked: bool) -> str | None:
        """保存済みOpen WebUI Keyをblockまたはunblockする。

        Args:
            username: Linuxユーザー名。
            blocked: blockする場合はTrue。

        Returns:
            正常ならNone、失敗時は安全化したエラーメッセージ。
        """
        key = self.key_store.read(username)
        if not key:
            return None
        endpoint = "/key/block" if blocked else "/key/unblock"
        action = "openwebui_key_block" if blocked else "openwebui_key_unblock"
        try:
            self.client.request(endpoint, {"key": key})
        except RuntimeError as exc:
            message = str(exc)
            lowered = message.lower()
            if any(
                (
                    marker in lowered
                    for marker in (
                        "http 401",
                        "http 404",
                        "token_not_found",
                        "not found",
                    )
                )
            ):
                self.key_store.remove(username)
                self.gateway.log_litellm_action(action, username, "missing")
                return None
            safe_error = self.gateway.safe_litellm_error(exc)
            self.gateway.log_litellm_action(action, username, "failed", safe_error)
            return safe_error
        self.gateway.log_litellm_action(action, username, "ok")
        return None


class EnsureOpenWebuiKeyUseCase:
    def __init__(
        self,
        *,
        client: LlmClient,
        gateway: LlmManagementGateway,
        key_store: KeyStore,
        generate_openwebui_key: IssueOpenWebuiKeyUseCase,
    ):
        self.client = client
        self.gateway = gateway
        self.key_store = key_store
        self.generate_openwebui_key = generate_openwebui_key

    def execute(self, username: str) -> tuple[str | None, str | None]:
        """有効な利用者の永続Open WebUI Keyを取得または発行する。

        Args:
            username: Open WebUIを起動するLinuxユーザー名。

        Returns:
            ``(平文Key, エラー)``。成功時のエラーはNone。
        """
        if not self.client.enabled():
            return (None, "LiteLLM Admin API が未設定です")
        user_err = self.gateway.ensure_user(username)
        if user_err:
            return (None, user_err)
        disabled, err = self.gateway.user_admin_disabled(username)
        if err:
            return (None, err)
        issuance_error = LlmAccess(username, disabled).issuance_error(
            "API 利用が管理者により停止されています"
        )
        if issuance_error:
            return (None, issuance_error)

        existing_key = self.key_store.read(username)
        if existing_key:
            _info, state, info_error = self.gateway.openwebui_key_info(
                username, existing_key
            )
            if state == "valid":
                return (existing_key, None)
            if state == "missing":
                self.key_store.remove(username)
                self.gateway.log_litellm_action(
                    "openwebui_key_validate", username, "missing"
                )
            elif state == "blocked":
                self.gateway.log_litellm_action(
                    "openwebui_key_validate", username, "blocked"
                )
                return (None, "Open WebUI 用 API key が無効化されています")
            elif state == "mismatch":
                self.gateway.log_litellm_action(
                    "openwebui_key_validate", username, "mismatch", info_error
                )
                return (
                    None,
                    info_error or "Open WebUI 用 API key の所有者が一致しません",
                )
            else:
                self.gateway.log_litellm_action(
                    "openwebui_key_validate", username, "failed", info_error
                )
                return (
                    None,
                    info_error or "Open WebUI 用 API key の確認に失敗しました",
                )

        key, err = self.generate_openwebui_key.execute(username)
        if err:
            return (None, err)
        write_err = self.key_store.write(username, key or "")
        if write_err:
            # 保存できなかったキーを有効なまま残さず、次の起動で再確認できるようにする。
            try:
                self.client.request("/key/block", {"key": key})
            except RuntimeError:
                pass
            self.gateway.log_litellm_action(
                "openwebui_key_store", username, "failed", write_err
            )
            return (None, write_err)
        self.gateway.log_litellm_action("openwebui_key_store", username, "ok")
        return (key, None)
