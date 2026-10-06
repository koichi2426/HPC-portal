"""LLM利用者・キー・モデルの管理と、応答解析・排他制御の契約。"""

import threading
from typing import Protocol


class LlmManagementGateway(Protocol):
    def ensure_user(self, username: str) -> str | None:
        """LiteLLMユーザーが存在する状態を保証する。

        Args:
            username: Linuxユーザー名と共通のuser_id。

        Returns:
            正常または既存ならNone、失敗時はエラーメッセージ。
        """
        ...

    def metadata(self, value) -> dict:
        """LiteLLMのmetadata値を辞書へ正規化する。

        Args:
            value: 辞書またはJSON文字列。

        Returns:
            metadata辞書。不正値の場合は空辞書。
        """
        ...

    def user_metadata(self, username: str) -> tuple[dict, str | None]:
        """LiteLLMユーザーのmetadataを取得する。

        Args:
            username: Linuxユーザー名。

        Returns:
            ``(metadata, エラー)``。
        """
        ...

    def set_user_admin_disabled(self, username: str, disabled: bool) -> str | None:
        """ポータル管理者による停止状態をユーザーmetadataへ保存する。

        Args:
            username: Linuxユーザー名。
            disabled: 停止状態にする場合はTrue。

        Returns:
            正常ならNone、失敗時はエラーメッセージ。
        """
        ...

    def user_admin_disabled(self, username: str) -> tuple[bool, str | None]:
        """ユーザーが管理者により停止されているか取得する。

        Args:
            username: Linuxユーザー名。

        Returns:
            ``(停止状態, エラー)``。
        """
        ...

    def iter_key_records(self, value):
        """LiteLLMレスポンスからKeyレコードを再帰的に列挙する。

        Args:
            value: LiteLLM APIのJSON互換値。

        Yields:
            Key識別子を含む辞書。
        """
        ...

    def key_identifier(self, record: dict) -> str:
        """Key操作に使える識別子をレコードから取得する。

        Args:
            record: LiteLLMのKeyレコード。

        Returns:
            利用可能な識別子。存在しなければ空文字列。
        """
        ...

    def key_belongs_to_user(self, record: dict, username: str) -> bool:
        """LLMキーが対象ユーザーの所有物か確認する。

        Args:
            record: LiteLLMから取得したキーレコード。
            username: 対象のLinuxユーザー名。

        Returns:
            所有者情報が一致する場合はTrue。
        """
        ...

    def list_user_keys(self, username: str) -> tuple[list[dict], str | None]:
        """利用者に属するLiteLLM Keyを列挙する。

        Args:
            username: 検索対象のuser_idまたはLinuxユーザー名。

        Returns:
            ``(Keyレコード一覧, エラー)``。
        """
        ...

    def is_portal_external_key(self, record: dict, username: str) -> bool:
        """LLMキーのaliasが対象ユーザーの外部利用用か確認する。

        Args:
            record: LiteLLMから取得したキーレコード。
            username: 対象のLinuxユーザー名。

        Returns:
            aliasがユーザー名と一致する場合はTrue。
        """
        ...

    def external_api_key_lock(self, username: str):
        """同じユーザーの外部API key再発行を直列化する。

        Args:
            username: 対象のLinuxユーザー名。

        Returns:
            ユーザー単位の排他ロック。
        """
        ...

    def set_key_metadata(self, record: dict, disabled: bool) -> str | None:
        """Keyの補助metadataへ管理者停止状態を反映する。

        Args:
            record: 更新対象のKeyレコード。
            disabled: 停止状態にする場合はTrue。

        Returns:
            正常ならNone、失敗時はエラーメッセージ。
        """
        ...

    def model_records(self, value):
        """LiteLLM の model 一覧レスポンスから model record を取り出す。

        Args:
            value: LiteLLM API から返った JSON 互換オブジェクト。

        Yields:
            model ID を含む可能性がある dict を順に返す generator。
        """
        ...

    def model_lock(self, model: str) -> threading.Lock:
        """モデル単位のLiteLLM登録ロックを返す。

        Args:
            model: Ollamaモデル名。

        Returns:
            同一プロセス内の重複登録を防ぐロック。
        """
        ...

    def model_info(self) -> tuple[dict | list | None, str | None]:
        """LiteLLMのdeployment情報を取得する。

        Returns:
            ``(APIレスポンス, エラー)``。取得成功時のエラーはNone。
        """
        ...

    def ollama_deployments(self, value, model: str) -> list[dict]:
        """指定Ollamaモデルに対応するLiteLLM deploymentを抽出する。

        Args:
            value: ``/v1/model/info`` のJSON互換レスポンス。
            model: 公開モデル名およびOllamaモデル名。

        Returns:
            backend、ID、DB由来か、ポータル管理かを持つdeployment一覧。
        """
        ...

    def delete_deployments(self, deployment_ids: list[str]) -> str | None:
        """LiteLLMのDB deploymentをID指定で削除する。

        Args:
            deployment_ids: 削除するLiteLLM model ID。

        Returns:
            正常時はNone、失敗時は安全化したエラーメッセージ。
        """
        ...

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
        ...

    def safe_litellm_error(self, error) -> str:
        """ログや画面へ返すエラーから Virtual Key らしい文字列を除去する。

        Args:
            error: 安全化するエラー情報。

        Returns:
            Virtual Keyを除去した画面・ログ用エラー。
        """
        ...

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
        ...
