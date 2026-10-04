"""LLMの利用者・キー・モデルを管理する操作手順。"""

import json
import logging
import re
import threading
import urllib.parse

from hpc_portal.application.ports.llm_management_ports import (
    KeyStore,
    LlmClient,
    ModelInventory,
)
from hpc_portal.application.ports.user_management_ports import AccountDirectory
from hpc_portal.domain.llm_models import HpcLlmModel

HPC_LITELLM_LOG = logging.getLogger("jupyterhub.hpc-litellm")
_HPC_LITELLM_OLLAMA_SOURCE = "hpc-portal-ollama"
_HPC_LITELLM_OLLAMA_BACKENDS = ("ollama_chat/", "ollama/")


class LlmManagementUseCase:
    def __init__(
        self,
        client: LlmClient,
        ollama: ModelInventory,
        key_store: KeyStore,
        accounts: AccountDirectory,
        ollama_base_url: str,
    ):
        self.client = client
        self.ollama = ollama
        self.key_store = key_store
        self.accounts = accounts
        self.ollama_base_url = ollama_base_url
        self.key_locks = {}
        self.key_locks_guard = threading.Lock()
        self.model_locks = {}
        self.model_locks_guard = threading.Lock()

    def ensure_user(self, username: str) -> str | None:
        """LiteLLMユーザーが存在する状態を保証する。

        Args:
            username: Linuxユーザー名と共通のuser_id。

        Returns:
            正常または既存ならNone、失敗時はエラーメッセージ。
        """
        if not self.client.enabled():
            return "LiteLLM Admin API が未設定のため API key は未発行です"
        payload = {
            "user_id": username,
            "user_email": f"{username}@hpc-portal.local",
            "user_role": "internal_user",
            "metadata": {
                "linux_username": username,
                "source": "hpc-portal",
                "admin_disabled": False,
            },
        }
        try:
            self.client.request("/user/new", payload)
            return None
        except RuntimeError as exc:
            # 既に存在する場合は key 発行に進める。LiteLLM の重複エラー文言はバージョンで揺れるため広めに許容する。
            msg = str(exc)
            if (
                "already" in msg.lower()
                or "exists" in msg.lower()
                or "duplicate" in msg.lower()
            ):
                return None
            return msg

    def metadata(self, value) -> dict:
        """LiteLLMのmetadata値を辞書へ正規化する。

        Args:
            value: 辞書またはJSON文字列。

        Returns:
            metadata辞書。不正値の場合は空辞書。
        """
        if isinstance(value, dict):
            return value
        if isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                return {}
        return {}

    def user_metadata(self, username: str) -> tuple[dict, str | None]:
        """LiteLLMユーザーのmetadataを取得する。

        Args:
            username: Linuxユーザー名。

        Returns:
            ``(metadata, エラー)``。
        """
        if not self.client.enabled():
            return {}, "LiteLLM Admin API が未設定です"
        quoted = urllib.parse.quote(username, safe="")
        try:
            data = self.client.request(f"/user/info?user_id={quoted}", method="GET")
        except RuntimeError as exc:
            return {}, str(exc)
        candidates = [
            data,
            data.get("user_info") if isinstance(data, dict) else None,
            data.get("info") if isinstance(data, dict) else None,
        ]
        for item in candidates:
            if isinstance(item, dict):
                metadata = self.metadata(item.get("metadata"))
                if metadata:
                    return metadata, None
        return {}, None

    def set_user_admin_disabled(self, username: str, disabled: bool) -> str | None:
        """ポータル管理者による停止状態をユーザーmetadataへ保存する。

        Args:
            username: Linuxユーザー名。
            disabled: 停止状態にする場合はTrue。

        Returns:
            正常ならNone、失敗時はエラーメッセージ。
        """
        err = self.ensure_user(username)
        if err:
            return err
        payload = {
            "user_id": username,
            "metadata": {
                "linux_username": username,
                "source": "hpc-portal",
                "admin_disabled": bool(disabled),
            },
        }
        try:
            self.client.request("/user/update", payload)
            return None
        except RuntimeError as exc:
            return str(exc)

    def user_admin_disabled(self, username: str) -> tuple[bool, str | None]:
        """ユーザーが管理者により停止されているか取得する。

        Args:
            username: Linuxユーザー名。

        Returns:
            ``(停止状態, エラー)``。
        """
        metadata, err = self.user_metadata(username)
        if err:
            return False, err
        # ユーザーmetadataを正本にする。過去にblockしたキーのmetadataを参照すると、
        # API利用を再有効化しても古いキーの停止フラグで誤って拒否されるため。
        return metadata.get("admin_disabled") is True, None

    def generate_key(self, username: str) -> tuple[str | None, str | None]:
        """利用者向け外部API用Virtual Keyを発行する。

        Args:
            username: Keyを所有するLinuxユーザー名。

        Returns:
            ``(平文Key, エラー)``。成功時のエラーはNone。
        """
        if not self.client.enabled():
            return None, "LiteLLM Admin API が未設定のため API key は未発行です"
        user_err = self.ensure_user(username)
        if user_err:
            return None, user_err
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
            return None, str(exc)
        key = data.get("key") or data.get("token")
        if not key:
            return None, "LiteLLM key 発行レスポンスに key が含まれていません"
        return key, None

    def iter_key_records(self, value):
        """LiteLLMレスポンスからKeyレコードを再帰的に列挙する。

        Args:
            value: LiteLLM APIのJSON互換値。

        Yields:
            Key識別子を含む辞書。
        """
        if isinstance(value, list):
            for item in value:
                yield from self.iter_key_records(item)
            return
        if not isinstance(value, dict):
            return
        if any(k in value for k in ("token", "key", "key_alias", "hashed_token")):
            yield value
        for key in ("keys", "data", "key_list", "keys_info", "tokens", "info"):
            child = value.get(key)
            if child is not None and child is not value:
                yield from self.iter_key_records(child)

    def key_identifier(self, record: dict) -> str:
        """Key操作に使える識別子をレコードから取得する。

        Args:
            record: LiteLLMのKeyレコード。

        Returns:
            利用可能な識別子。存在しなければ空文字列。
        """
        for field in ("key", "token", "key_name", "hashed_token", "token_id", "id"):
            value = record.get(field)
            if value:
                return str(value)
        return ""

    def key_belongs_to_user(self, record: dict, username: str) -> bool:
        """Keyレコードが利用者に属するか判定する。

        Args:
            record: LiteLLMのKeyレコード。
            username: Linuxユーザー名。

        Returns:
            user_id、alias、metadataのいずれかが一致すればTrue。
        """
        metadata = self.metadata(record.get("metadata"))
        candidates = {
            str(record.get("user_id") or ""),
            str(record.get("key_alias") or ""),
            str(metadata.get("linux_username") or ""),
        }
        return username in candidates

    def list_user_keys(self, username: str) -> tuple[list[dict], str | None]:
        """利用者に属するLiteLLM Keyを列挙する。

        Args:
            username: 検索対象のuser_idまたはLinuxユーザー名。

        Returns:
            ``(Keyレコード一覧, エラー)``。
        """
        if not self.client.enabled():
            return [], "LiteLLM Admin API が未設定です"
        quoted = urllib.parse.quote(username, safe="")
        errors = []
        succeeded = False
        for path in (
            f"/key/list?user_id={quoted}",
            "/key/list",
            f"/user/info?user_id={quoted}",
        ):
            try:
                data = self.client.request(path, method="GET")
            except RuntimeError as exc:
                errors.append(str(exc))
                continue
            succeeded = True
            records = [
                rec
                for rec in self.iter_key_records(data)
                if self.key_belongs_to_user(rec, username)
            ]
            if records:
                return records, None
        return [], errors[-1] if errors and not succeeded else None

    def is_portal_external_key(self, record: dict, username: str) -> bool:
        """ポータルで予約した外部API keyのaliasだけを再発行対象にする。

        Args:
            record: LiteLLMのKeyレコード。
            username: 対象のLinuxユーザー名。

        Returns:
            ポータル発行の外部API KeyならTrue。
        """
        # LiteLLMの一覧APIはバージョンによってmetadataを返さないことがあるため、
        # metadata.sourceではなく予約済みのaliasを正本にする。
        # Open WebUI用は常に ``openwebui-<username>`` であり、この条件に一致しない。
        return str(record.get("key_alias") or "") == username

    def user_external_api_state(self, username: str) -> tuple[str, str | None]:
        """管理画面向けに外部APIの利用状態を取得する。

        Args:
            username: 状態を取得するLinuxユーザー名。

        Returns:
            ``(状態, エラー)``。状態は enabled / disabled / unissued / unknown。
        """
        disabled, err = self.user_admin_disabled(username)
        if err:
            lowered = str(err).lower()
            if "http 404" in lowered or "not found" in lowered:
                return "unissued", None
            return "unknown", self.safe_litellm_error(err)
        if disabled:
            return "disabled", None
        records, err = self.list_user_keys(username)
        if err:
            return "unknown", self.safe_litellm_error(err)
        if any(self.is_portal_external_key(record, username) for record in records):
            return "enabled", None
        return "unissued", None

    def external_api_key_lock(self, username: str):
        """同じユーザーの外部API key再発行を直列化する。

        Args:
            username: 対象のLinuxユーザー名。

        Returns:
            ユーザー単位の排他ロック。
        """
        with self.key_locks_guard:
            return self.key_locks.setdefault(username, threading.Lock())

    def delete_portal_external_keys(self, username: str) -> str | None:
        """再発行前にポータル発行の外部API keyをblockして削除・確認する。

        Open WebUI用（source=hpc-portal-openwebui）のkeyには一切触れない。

        Args:
            username: 対象のLinuxユーザー名。

        Returns:
            正常ならNone、失敗時はエラーメッセージ。
        """
        records, err = self.list_user_keys(username)
        if err:
            return err
        targets = [
            record
            for record in records
            if self.is_portal_external_key(record, username)
        ]
        if not targets:
            return None

        key_ids = []
        for record in targets:
            key_id = self.key_identifier(record)
            if key_id:
                key_ids.append(key_id)

        for key_id in key_ids:
            try:
                self.client.request("/key/block", {"key": key_id})
            except RuntimeError as exc:
                safe_error = self.safe_litellm_error(exc)
                self.log_litellm_action(
                    "api_key_regenerate_block", username, "failed", safe_error
                )
                return safe_error

        try:
            # LiteLLMはalias指定で削除できる。key値やhashの形式差に依存しないため、
            # 旧バージョンの一覧レスポンスでも確実に同じaliasを解放できる。
            self.client.request("/key/delete", {"key_aliases": [username]})
        except RuntimeError as exc:
            safe_error = self.safe_litellm_error(exc)
            self.log_litellm_action(
                "api_key_regenerate_delete", username, "failed", safe_error
            )
            return safe_error

        remaining, list_err = self.list_user_keys(username)
        if list_err:
            safe_error = self.safe_litellm_error(list_err)
            self.log_litellm_action(
                "api_key_regenerate_verify", username, "failed", safe_error
            )
            return safe_error
        if any(self.is_portal_external_key(record, username) for record in remaining):
            message = "古い外部API keyの削除を確認できません"
            self.log_litellm_action(
                "api_key_regenerate_verify", username, "failed", message
            )
            return message
        self.log_litellm_action("api_key_regenerate_delete", username, "ok")
        return None

    def set_key_metadata(self, record: dict, disabled: bool) -> str | None:
        """Keyの補助metadataへ管理者停止状態を反映する。

        Args:
            record: 更新対象のKeyレコード。
            disabled: 停止状態にする場合はTrue。

        Returns:
            正常ならNone、失敗時はエラーメッセージ。
        """
        key_id = self.key_identifier(record)
        if not key_id:
            return "LiteLLM key の識別子が取得できません"
        metadata = dict(self.metadata(record.get("metadata")))
        metadata.update(
            {
                "linux_username": metadata.get("linux_username")
                or record.get("user_id")
                or record.get("key_alias")
                or "",
                "source": metadata.get("source") or "hpc-portal",
                "admin_disabled": bool(disabled),
            }
        )
        try:
            self.client.request("/key/update", {"key": key_id, "metadata": metadata})
            return None
        except RuntimeError as exc:
            return str(exc)

    def set_user_keys_blocked(
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
        records, err = self.list_user_keys(username)
        if err:
            return err
        endpoint = "/key/block" if blocked else "/key/unblock"
        action = "user_keys_block" if blocked else "user_keys_unblock"
        errors = []
        for record in records:
            metadata = self.metadata(record.get("metadata"))
            if (
                not include_openwebui
                and metadata.get("source") == "hpc-portal-openwebui"
            ):
                continue
            key_id = self.key_identifier(record)
            if not key_id:
                errors.append("LiteLLM key の識別子が取得できません")
                continue
            try:
                self.client.request(endpoint, {"key": key_id})
            except RuntimeError as exc:
                errors.append(str(exc))
                continue
            if mark_admin_disabled is not None:
                meta_err = self.set_key_metadata(record, mark_admin_disabled)
                if meta_err:
                    # 管理者停止フラグの正本は user metadata。key metadata は一覧確認用の補助なので、
                    # LiteLLM のバージョン差で更新に失敗しても block/unblock 成功を優先する。
                    pass
        if errors:
            joined = "; ".join(self.safe_litellm_error(error) for error in errors)
            self.log_litellm_action(action, username, "failed", joined)
            return joined
        self.log_litellm_action(action, username, "ok")
        return None

    def ensure_external_api_key(self, username: str) -> tuple[str | None, str | None]:
        """ポータル用外部API keyがなければ新規発行する。

        Args:
            username: keyを所有するLinuxユーザー名。

        Returns:
            新規発行した平文keyとエラーメッセージの組。既存keyがある場合は
            どちらもNoneを返す。
        """
        with self.external_api_key_lock(username):
            records, err = self.list_user_keys(username)
            if err:
                return None, err
            if any(self.is_portal_external_key(record, username) for record in records):
                return None, None
            return self.generate_key(username)

    def admin_set_api_access(
        self,
        username: str,
        enabled: bool,
    ) -> tuple[str | None, str | None]:
        """利用者単位で外部APIとOpen WebUIの利用可否を切り替える。

        Args:
            username: 対象のLinuxユーザー名。
            enabled: 有効化する場合はTrue。

        Returns:
            ``(新規発行したAPI key, エラー)``。既存keyの再有効化時はkeyを返さない。
        """
        if not self.client.enabled():
            return None, "LiteLLM Admin API が未設定です"
        try:
            self.accounts.getpwnam(username)
        except KeyError:
            return None, "ユーザーが見つかりません"
        if not enabled:
            # 先にユーザーを停止状態へし、新規Open WebUI起動を拒否する。
            user_err = self.set_user_admin_disabled(username, True)
            if user_err:
                self.log_litellm_action("api_disable", username, "failed", user_err)
                return None, user_err
            external_err = self.set_user_keys_blocked(
                username,
                blocked=True,
                mark_admin_disabled=True,
                include_openwebui=False,
            )
            openwebui_err = self.set_openwebui_key_blocked(username, True)
            errors = [error for error in (external_err, openwebui_err) if error]
            if errors:
                joined = "; ".join(errors)
                self.log_litellm_action("api_disable", username, "partial", joined)
                return None, joined
            self.log_litellm_action("api_disable", username, "ok")
            return None, None

        # 未登録ユーザーも同じ操作で有効化できるよう、先にLiteLLMユーザーを用意する。
        user_err = self.ensure_user(username)
        if user_err:
            self.log_litellm_action("api_enable", username, "failed", user_err)
            return None, user_err

        # 再有効化では保存済みの同じOpen WebUI keyをunblockする。どれかが失敗したら
        # user metadataは停止状態のままにし、部分的な有効化を避ける。
        external_err = self.set_user_keys_blocked(
            username,
            blocked=False,
            mark_admin_disabled=False,
            include_openwebui=False,
        )
        openwebui_err = self.set_openwebui_key_blocked(username, False)
        errors = [error for error in (external_err, openwebui_err) if error]
        if errors:
            joined = "; ".join(errors)
            self.set_user_admin_disabled(username, True)
            self.set_user_keys_blocked(
                username,
                blocked=True,
                mark_admin_disabled=True,
                include_openwebui=False,
            )
            self.set_openwebui_key_blocked(username, True)
            self.log_litellm_action("api_enable", username, "failed", joined)
            return None, joined
        user_err = self.set_user_admin_disabled(username, False)
        if user_err:
            # user metadata更新失敗時は、先に有効化したkeyを再停止する。
            self.set_user_keys_blocked(
                username,
                blocked=True,
                mark_admin_disabled=True,
                include_openwebui=False,
            )
            self.set_openwebui_key_blocked(username, True)
            self.log_litellm_action("api_enable", username, "failed", user_err)
            return None, user_err

        api_key, key_err = self.ensure_external_api_key(username)
        if key_err:
            # 有効化とkey発行を一操作として扱い、発行失敗時は停止状態へ戻す。
            self.set_user_admin_disabled(username, True)
            self.set_user_keys_blocked(
                username,
                blocked=True,
                mark_admin_disabled=True,
                include_openwebui=False,
            )
            self.set_openwebui_key_blocked(username, True)
            safe_error = self.safe_litellm_error(key_err)
            self.log_litellm_action(
                "api_enable_key_generate", username, "failed", safe_error
            )
            return None, safe_error
        if api_key:
            self.log_litellm_action("api_enable_key_generate", username, "ok")
        self.log_litellm_action("api_enable", username, "ok")
        return api_key, None

    def regenerate_own_key(self, username: str) -> tuple[str | None, str | None]:
        """外部API用Keyを安全に削除して同じaliasで再発行する。

        Args:
            username: 再発行する本人のLinuxユーザー名。

        Returns:
            ``(新しい平文Key, エラー)``。Open WebUI Keyは変更しない。
        """
        with self.external_api_key_lock(username):
            if not self.client.enabled():
                return None, "LiteLLM Admin API が未設定です"
            try:
                self.accounts.getpwnam(username)
            except KeyError:
                return None, "ユーザーが見つかりません"
            disabled, err = self.user_admin_disabled(username)
            if err:
                return None, err
            if disabled:
                return None, "API key は管理者により無効化されています"
            err = self.delete_portal_external_keys(username)
            if err:
                return None, err
            key, err = self.generate_key(username)
            if err:
                self.log_litellm_action(
                    "api_key_regenerate_generate", username, "failed", err
                )
                return None, err
            self.log_litellm_action("api_key_regenerate_generate", username, "ok")
            return key, None

    def delete_user_keys(self, username: str) -> str | None:
        """ユーザー削除時に関連Virtual Keyと保存ファイルを破棄する。

        Args:
            username: 削除対象のLinuxユーザー名。

        Returns:
            正常ならNone、無効化または削除失敗時はエラーメッセージ。
        """
        self.key_store.remove(username)
        if not self.client.enabled():
            return "LiteLLM Admin API が未設定のため key 無効化は未確認です"
        block_err = self.set_user_keys_blocked(
            username, blocked=True, mark_admin_disabled=True
        )
        payload_candidates = (
            {"user_ids": [username]},
            {"user_id": username},
        )
        last_err = None
        for payload in payload_candidates:
            try:
                self.client.request("/user/delete", payload)
                return block_err
            except RuntimeError as exc:
                last_err = str(exc)
        return block_err or last_err or "LiteLLM user/key 無効化に失敗しました"

    def model_records(self, value):
        """LiteLLM の model 一覧レスポンスから model record を取り出す。

        Args:
            value: LiteLLM API から返った JSON 互換オブジェクト。

        Yields:
            model ID を含む可能性がある dict を順に返す generator。
        """
        if isinstance(value, dict):
            for key in ("data", "models", "model_list"):
                if key in value:
                    yield from self.model_records(value[key])
            if any(k in value for k in ("id", "model_name", "litellm_params")):
                yield value
            return
        if isinstance(value, list):
            for item in value:
                yield from self.model_records(item)
            return
        if isinstance(value, str) and value.strip():
            yield {"id": value.strip()}

    def list_models(self) -> tuple[list[dict], str | None]:
        """ユーザー画面に表示する LiteLLM model 一覧を取得する。

        LiteLLM の `/models` はバージョンや設定によって response shape が揺れるため、
        複数の候補フィールドから model ID を取り出して重複を除去する。

        Returns:
            1要素目は `id` と `owned_by` を持つ model dict の list。
            2要素目は取得失敗時のエラーメッセージ。成功時は None。
        """
        if not self.client.enabled():
            return [], "LiteLLM Admin API が未設定です"
        try:
            data = self.client.request("/models", method="GET")
        except RuntimeError as exc:
            return [], str(exc)
        models = []
        seen = set()
        for record in self.model_records(data):
            model_id = (
                record.get("id")
                or record.get("model_name")
                or record.get("model")
                or ""
            )
            model_id = str(model_id).strip()
            if not model_id or model_id in seen:
                continue
            seen.add(model_id)
            models.append(
                HpcLlmModel.model_validate(
                    {
                        "id": model_id,
                        "owned_by": str(
                            record.get("owned_by") or record.get("provider") or ""
                        ).strip(),
                    }
                ).model_dump()
            )
        models.sort(key=lambda item: item["id"])
        return models, None

    def model_lock(self, model: str) -> threading.Lock:
        """モデル単位のLiteLLM登録ロックを返す。

        Args:
            model: Ollamaモデル名。

        Returns:
            同一プロセス内の重複登録を防ぐロック。
        """
        with self.model_locks_guard:
            return self.model_locks.setdefault(model, threading.Lock())

    def model_info(self) -> tuple[dict | list | None, str | None]:
        """LiteLLMのdeployment情報を取得する。

        Returns:
            ``(APIレスポンス, エラー)``。取得成功時のエラーはNone。
        """
        try:
            return self.client.request("/v1/model/info", method="GET"), None
        except RuntimeError as exc:
            return None, self.safe_litellm_error(exc)

    def ollama_deployments(self, value, model: str) -> list[dict]:
        """指定Ollamaモデルに対応するLiteLLM deploymentを抽出する。

        Args:
            value: ``/v1/model/info`` のJSON互換レスポンス。
            model: 公開モデル名およびOllamaモデル名。

        Returns:
            backend、ID、DB由来か、ポータル管理かを持つdeployment一覧。
        """
        deployments = []
        expected_backends = {prefix + model for prefix in _HPC_LITELLM_OLLAMA_BACKENDS}
        for record in self.model_records(value):
            litellm_params = record.get("litellm_params") or {}
            model_info = record.get("model_info") or {}
            if not isinstance(litellm_params, dict) or not isinstance(model_info, dict):
                continue
            if str(record.get("model_name") or "") != model:
                continue
            backend = str(litellm_params.get("model") or "")
            if backend not in expected_backends:
                continue
            is_db_model = bool(model_info.get("db_model"))
            source = str(model_info.get("source") or "")
            deployments.append(
                {
                    "id": str(model_info.get("id") or "").strip(),
                    "backend": backend,
                    "db_model": is_db_model,
                    "portal_managed": is_db_model
                    and source == _HPC_LITELLM_OLLAMA_SOURCE,
                    "supports_function_calling": bool(
                        model_info.get("supports_function_calling")
                    ),
                }
            )
        return deployments

    def delete_deployments(self, deployment_ids: list[str]) -> str | None:
        """LiteLLMのDB deploymentをID指定で削除する。

        Args:
            deployment_ids: 削除するLiteLLM model ID。

        Returns:
            正常時はNone、失敗時は安全化したエラーメッセージ。
        """
        for model_id in dict.fromkeys(item for item in deployment_ids if item):
            try:
                self.client.request("/model/delete", {"id": model_id})
            except RuntimeError as exc:
                return self.safe_litellm_error(exc)
        return None

    def register_ollama_model(self, model: str) -> tuple[dict | None, str | None]:
        """OllamaモデルをLiteLLMのchat backendへ安全に同期する。

        正しい ``ollama_chat/`` deploymentを作成・確認してから、旧
        ``ollama/`` deploymentを削除する。ポータル管理外の設定モデルや手動登録
        モデルは変更しない。

        Args:
            model: Ollamaに登録済みのモデル名。

        Returns:
            ``(同期状態, エラー)``。状態は ``registered`` または
            ``already_registered``。
        """
        model = str(model or "").strip()
        if not model:
            return None, "モデル名が必要です"
        if re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", model) is None:
            return None, "モデル名に使用できない文字が含まれています"
        if not self.client.enabled():
            return None, "LiteLLM Admin API が未設定です"

        with self.model_lock(model):
            # 削除とpull完了が競合した場合に、Ollamaにないモデルを再登録しない。

            exists, ollama_err = self.ollama.has_model(model)
            if ollama_err or not exists:
                return None, ollama_err or f"Ollamaにモデル {model} がありません"
            supports_tools, capability_err = self.ollama.model_supports_tools(model)
            if capability_err or supports_tools is None:
                return None, capability_err or "Ollamaモデルの機能を確認できません"

            response, err = self.model_info()
            if err:
                return None, err
            deployments = self.ollama_deployments(response, model)
            target_backend = f"ollama_chat/{model}"
            correct = [
                item for item in deployments if item["backend"] == target_backend
            ]
            legacy = [
                item
                for item in deployments
                if item["portal_managed"] and item["backend"] == f"ollama/{model}"
            ]
            unmanaged_legacy = [
                item
                for item in deployments
                if not item["portal_managed"] and item["backend"] == f"ollama/{model}"
            ]
            if unmanaged_legacy:
                return None, (
                    "同名の設定ファイル由来または手動登録されたollama/モデルがあるため、"
                    "LiteLLM管理画面で確認してください"
                )

            created = False
            if not correct:
                payload = {
                    "model_name": model,
                    "litellm_params": {
                        "model": target_backend,
                        "api_base": self.ollama_base_url,
                    },
                    "model_info": {
                        "source": _HPC_LITELLM_OLLAMA_SOURCE,
                        "supports_function_calling": supports_tools,
                    },
                }
                try:
                    self.client.request("/model/new", payload)
                except RuntimeError as exc:
                    return None, self.safe_litellm_error(exc)
                created = True

            # 作成後の再取得で正しいDB deploymentを確認するまで旧設定は残す。
            verified_response, err = self.model_info()
            if err:
                return None, f"登録後の確認に失敗しました: {err}"
            verified = self.ollama_deployments(verified_response, model)
            verified_correct = [
                item for item in verified if item["backend"] == target_backend
            ]
            if not verified_correct:
                return (
                    None,
                    "LiteLLMへ追加しましたが、正しい接続方式を確認できませんでした",
                )

            verified_legacy = [
                item
                for item in verified
                if item["portal_managed"] and item["backend"] == f"ollama/{model}"
            ]
            delete_err = self.delete_deployments(
                [item["id"] for item in verified_legacy]
            )
            if delete_err:
                return None, "旧LiteLLMモデルの削除に失敗しました: " + delete_err

            migrated = len({item["id"] for item in legacy if item["id"]})
            HPC_LITELLM_LOG.info(
                "action=model_sync model=%s backend=%s supports_tools=%s migrated=%d result=ok",
                model,
                target_backend,
                supports_tools,
                migrated,
            )
            return {
                "state": "registered" if created or migrated else "already_registered",
                "model": model,
                "backend": target_backend,
                "supports_tools": supports_tools,
                "migrated": migrated,
                "message": "LiteLLMへ同期しました"
                if created or migrated
                else "LiteLLM同期済み",
            }, None

    def sync_ollama_models(self) -> tuple[dict | None, str | None]:
        """Ollamaに存在する全モデルをLiteLLMへ同期する。

        Returns:
            ``(モデル別結果と件数, エラー)``。一覧取得失敗時のみ全体エラーを返す。
        """

        model_names, err = self.ollama.model_names()
        if err:
            return None, err
        results = []
        for model in model_names:
            state, model_err = self.register_ollama_model(model)
            if model_err:
                results.append(
                    {"model": model, "state": "failed", "message": model_err}
                )
            else:
                results.append(state or {"model": model, "state": "failed"})
        failed = sum(item.get("state") == "failed" for item in results)
        changed = sum(item.get("state") == "registered" for item in results)
        HPC_LITELLM_LOG.info(
            "action=models_sync total=%d changed=%d failed=%d result=%s",
            len(results),
            changed,
            failed,
            "partial" if failed else "ok",
        )
        return {
            "total": len(results),
            "changed": changed,
            "failed": failed,
            "results": results,
        }, None

    def delete_ollama_model(self, model: str) -> str | None:
        """Ollamaモデルに対応するLiteLLMのDBモデルを削除する。

        Args:
            model: Ollamaから削除するモデル名。

        Returns:
            正常または該当モデルなしならNone、失敗時はエラーメッセージ。
        """
        model = str(model or "").strip()
        if not model:
            return "モデル名が必要です"
        if re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", model) is None:
            return "モデル名に使用できない文字が含まれています"
        if not self.client.enabled():
            return "LiteLLM Admin API が未設定です"

        with self.model_lock(model):
            try:
                response = self.client.request("/v1/model/info", method="GET")
            except RuntimeError as exc:
                return self.safe_litellm_error(exc)
            deployments = self.ollama_deployments(response, model)
            deployment_ids = [
                item["id"]
                for item in deployments
                if item["portal_managed"] and item["id"]
            ]
            delete_err = self.delete_deployments(deployment_ids)
            if delete_err:
                return delete_err
            if deployment_ids:
                HPC_LITELLM_LOG.info(
                    "action=model_delete model=%s deployments=%d result=ok",
                    model,
                    len(set(deployment_ids)),
                )
            return None

    def generate_openwebui_key(self, username: str) -> tuple[str | None, str | None]:
        """ユーザーごとに再利用する Open WebUI 専用 Virtual Key を初回だけ発行する。

        Args:
            username: 対象のLinuxユーザー名。

        Returns:
            発行したKeyとエラーメッセージの組。
        """
        if not self.client.enabled():
            return None, "LiteLLM Admin API が未設定です"
        user_err = self.ensure_user(username)
        if user_err:
            return None, user_err
        disabled, err = self.user_admin_disabled(username)
        if err:
            return None, err
        if disabled:
            return None, "API 利用が管理者により停止されています"
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
            return None, str(exc)
        key = data.get("key") or data.get("token")
        if not key:
            return None, "Open WebUI 用 key 発行レスポンスに key が含まれていません"
        self.log_litellm_action("openwebui_key_generate", username, "ok")
        return key, None

    def openwebui_key_info(
        self, username: str, key: str
    ) -> tuple[dict | None, str, str | None]:
        """保存keyのLiteLLM状態を返す: valid / missing / blocked / mismatch / error。

        Args:
            username: 対象のLinuxユーザー名。
            key: LiteLLM Virtual Key。

        Returns:
            Key情報、状態、エラーメッセージの組。
        """
        quoted = urllib.parse.quote(key, safe="")
        try:
            data = self.client.request(f"/key/info?key={quoted}", method="GET")
        except RuntimeError as exc:
            message = str(exc)
            lowered = message.lower()
            if any(
                marker in lowered
                for marker in ("http 401", "http 404", "token_not_found", "not found")
            ):
                return None, "missing", None
            return None, "error", self.safe_litellm_error(exc)
        info = data.get("info") if isinstance(data, dict) else None
        if not isinstance(info, dict):
            info = data if isinstance(data, dict) else {}
        record_user_id = str(info.get("user_id") or data.get("user_id") or "")
        if not record_user_id:
            return info, "mismatch", "保存keyにuser_idが設定されていません"
        if record_user_id != username:
            return info, "mismatch", "保存keyのuser_idがログインユーザーと一致しません"
        blocked_value = info.get("blocked", data.get("blocked"))
        blocked = blocked_value is True or str(blocked_value).strip().lower() in {
            "true",
            "1",
            "yes",
        }
        if blocked:
            return info, "blocked", None
        return info, "valid", None

    def set_openwebui_key_blocked(self, username: str, blocked: bool) -> str | None:
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
                marker in lowered
                for marker in ("http 401", "http 404", "token_not_found", "not found")
            ):
                self.key_store.remove(username)
                self.log_litellm_action(action, username, "missing")
                return None
            safe_error = self.safe_litellm_error(exc)
            self.log_litellm_action(action, username, "failed", safe_error)
            return safe_error
        self.log_litellm_action(action, username, "ok")
        return None

    def get_openwebui_key(self, username: str) -> tuple[str | None, str | None]:
        """有効な利用者の永続Open WebUI Keyを取得または発行する。

        Args:
            username: Open WebUIを起動するLinuxユーザー名。

        Returns:
            ``(平文Key, エラー)``。成功時のエラーはNone。
        """
        if not self.client.enabled():
            return None, "LiteLLM Admin API が未設定です"
        user_err = self.ensure_user(username)
        if user_err:
            return None, user_err
        disabled, err = self.user_admin_disabled(username)
        if err:
            return None, err
        if disabled:
            return None, "API 利用が管理者により停止されています"
        existing_key = self.key_store.read(username)
        if existing_key:
            _info, state, info_error = self.openwebui_key_info(username, existing_key)
            if state == "valid":
                return existing_key, None
            if state == "missing":
                self.key_store.remove(username)
                self.log_litellm_action("openwebui_key_validate", username, "missing")
            elif state == "blocked":
                self.log_litellm_action("openwebui_key_validate", username, "blocked")
                return None, "Open WebUI 用 API key が無効化されています"
            elif state == "mismatch":
                self.log_litellm_action(
                    "openwebui_key_validate", username, "mismatch", info_error
                )
                return (
                    None,
                    info_error or "Open WebUI 用 API key の所有者が一致しません",
                )
            else:
                self.log_litellm_action(
                    "openwebui_key_validate", username, "failed", info_error
                )
                return None, info_error or "Open WebUI 用 API key の確認に失敗しました"
        key, err = self.generate_openwebui_key(username)
        if err:
            return None, err
        write_err = self.key_store.write(username, key or "")
        if write_err:
            try:
                self.client.request("/key/block", {"key": key})
            except RuntimeError:
                pass
            self.log_litellm_action(
                "openwebui_key_store", username, "failed", write_err
            )
            return None, write_err
        self.log_litellm_action("openwebui_key_store", username, "ok")
        return key, None

    def safe_litellm_error(self, error) -> str:
        """ログや画面へ返すエラーから Virtual Key らしい文字列を除去する。

        Args:
            error: 安全化するエラー情報。

        Returns:
            Virtual Keyを除去した画面・ログ用エラー。
        """
        return re.sub(r"sk-[A-Za-z0-9._~-]+", "sk-[REDACTED]", str(error or ""))[:500]

    def log_litellm_action(
        self, action: str, username: str, result: str, error=None
    ) -> None:
        """秘密値を除去してLiteLLM操作ログを記録する。

        Args:
            action: 操作名。
            username: 対象のLinuxユーザー名。
            result: ok、failed、partialなどの結果。
            error: 任意のエラー情報。
        """
        message = "action=%s user=%s result=%s"
        args = [action, username, result]
        if error:
            message += " error=%s"
            args.append(self.safe_litellm_error(error))
        if result == "ok":
            HPC_LITELLM_LOG.info(message, *args)
        else:
            HPC_LITELLM_LOG.warning(message, *args)
