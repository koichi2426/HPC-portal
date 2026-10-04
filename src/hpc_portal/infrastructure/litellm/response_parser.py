"""LiteLLMのバージョンごとに異なるJSON応答を正規化する。"""

import json

from hpc_portal.domain.llm.access import LlmKey

_HPC_LITELLM_OLLAMA_SOURCE = "hpc-portal-ollama"
_HPC_LITELLM_OLLAMA_BACKENDS = ("ollama_chat/", "ollama/")


class LiteLlmResponseParser:
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
        if any((k in value for k in ("token", "key", "key_alias", "hashed_token"))):
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
            if any((k in value for k in ("id", "model_name", "litellm_params"))):
                yield value
            return
        if isinstance(value, list):
            for item in value:
                yield from self.model_records(item)
            return
        if isinstance(value, str) and value.strip():
            yield {"id": value.strip()}

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

    def key(self, record):
        metadata = self.metadata(record.get("metadata"))
        return LlmKey(
            frozenset(
                {
                    str(record.get("user_id") or ""),
                    str(record.get("key_alias") or ""),
                    str(metadata.get("linux_username") or ""),
                }
            ),
            str(record.get("key_alias") or ""),
            str(metadata.get("source") or ""),
        )
