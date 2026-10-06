"""LiteLLMの管理通信・応答解析・操作の排他制御。"""

import logging
import re
import threading
import urllib.parse

from hpc_portal.domain.llm.access import LlmAccess
from hpc_portal.infrastructure.litellm.response_parser import LiteLlmResponseParser

HPC_LITELLM_LOG = logging.getLogger("jupyterhub.hpc-litellm")


class LiteLlmManagementGateway(LiteLlmResponseParser):
    def __init__(self, client, ollama, key_store, accounts, ollama_base_url: str):
        """LLM管理の依存と、ユーザー・モデル単位の共有ロックを準備する。

        Args:
            client: LiteLLM管理APIへ接続するクライアント。
            ollama: 共有Ollamaの実行状態とモデル情報を取得する接続先。
            key_store: Open WebUI専用キーを読み書きする保存先。
            accounts: Linuxユーザーの照合・作成・変更を行う接続先。
            ollama_base_url: LiteLLMから接続するOllamaのベースURL。
        """
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
            msg = str(exc)
            if (
                "already" in msg.lower()
                or "exists" in msg.lower()
                or "duplicate" in msg.lower()
            ):
                return None
            return msg

    def user_metadata(self, username: str) -> tuple[dict, str | None]:
        """LiteLLMユーザーのmetadataを取得する。

        Args:
            username: Linuxユーザー名。

        Returns:
            ``(metadata, エラー)``。
        """
        if not self.client.enabled():
            return ({}, "LiteLLM Admin API が未設定です")
        quoted = urllib.parse.quote(username, safe="")
        try:
            data = self.client.request(f"/user/info?user_id={quoted}", method="GET")
        except RuntimeError as exc:
            return ({}, str(exc))
        candidates = [
            data,
            data.get("user_info") if isinstance(data, dict) else None,
            data.get("info") if isinstance(data, dict) else None,
        ]
        for item in candidates:
            if isinstance(item, dict):
                metadata = self.metadata(item.get("metadata"))
                if metadata:
                    return (metadata, None)
        return ({}, None)

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
            return (False, err)
        return (
            LlmAccess(username, metadata.get("admin_disabled") is True).disabled,
            None,
        )

    def key_belongs_to_user(self, record: dict, username: str) -> bool:
        """LLMキーが対象ユーザーの所有物か確認する。

        Args:
            record: LiteLLMから取得したキーレコード。
            username: 対象のLinuxユーザー名。

        Returns:
            所有者情報が一致する場合はTrue。
        """
        return self.key(record).belongs_to(username)

    def list_user_keys(self, username: str) -> tuple[list[dict], str | None]:
        """利用者に属するLiteLLM Keyを列挙する。

        Args:
            username: 検索対象のuser_idまたはLinuxユーザー名。

        Returns:
            ``(Keyレコード一覧, エラー)``。
        """
        if not self.client.enabled():
            return ([], "LiteLLM Admin API が未設定です")
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
                return (records, None)
        return ([], errors[-1] if errors and (not succeeded) else None)

    def is_portal_external_key(self, record: dict, username: str) -> bool:
        """LLMキーのaliasが対象ユーザーの外部利用用か確認する。

        Args:
            record: LiteLLMから取得したキーレコード。
            username: 対象のLinuxユーザー名。

        Returns:
            aliasがユーザー名と一致する場合はTrue。
        """
        return self.key(record).is_external_key_for(username)

    def external_api_key_lock(self, username: str):
        """同じユーザーの外部API key再発行を直列化する。

        Args:
            username: 対象のLinuxユーザー名。

        Returns:
            ユーザー単位の排他ロック。
        """
        with self.key_locks_guard:
            return self.key_locks.setdefault(username, threading.Lock())

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
            return (self.client.request("/v1/model/info", method="GET"), None)
        except RuntimeError as exc:
            return (None, self.safe_litellm_error(exc))

    def delete_deployments(self, deployment_ids: list[str]) -> str | None:
        """LiteLLMのDB deploymentをID指定で削除する。

        Args:
            deployment_ids: 削除するLiteLLM model ID。

        Returns:
            正常時はNone、失敗時は安全化したエラーメッセージ。
        """
        for model_id in dict.fromkeys((item for item in deployment_ids if item)):
            try:
                self.client.request("/model/delete", {"id": model_id})
            except RuntimeError as exc:
                return self.safe_litellm_error(exc)
        return None

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
                return (None, "missing", None)
            return (None, "error", self.safe_litellm_error(exc))
        info = data.get("info") if isinstance(data, dict) else None
        if not isinstance(info, dict):
            info = data if isinstance(data, dict) else {}
        record_user_id = str(info.get("user_id") or data.get("user_id") or "")
        if not record_user_id:
            return (info, "mismatch", "保存keyにuser_idが設定されていません")
        if record_user_id != username:
            return (
                info,
                "mismatch",
                "保存keyのuser_idがログインユーザーと一致しません",
            )
        blocked_value = info.get("blocked", data.get("blocked"))
        blocked = blocked_value is True or str(blocked_value).strip().lower() in {
            "true",
            "1",
            "yes",
        }
        if blocked:
            return (info, "blocked", None)
        return (info, "valid", None)

    def safe_litellm_error(self, error) -> str:
        """ログや画面へ返すエラーから Virtual Key らしい文字列を除去する。

        Args:
            error: 安全化するエラー情報。

        Returns:
            Virtual Keyを除去した画面・ログ用エラー。
        """
        return re.sub("sk-[A-Za-z0-9._~-]+", "sk-[REDACTED]", str(error or ""))[:500]

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
