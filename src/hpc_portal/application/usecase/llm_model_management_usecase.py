"""LLMモデルの登録・同期・登録解除の操作手順。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from hpc_portal.application.ports.llm_gateway_ports import LlmManagementGateway
from hpc_portal.application.ports.llm_management_ports import LlmClient, ModelInventory
from hpc_portal.application.ports.ollama_management_ports import OllamaBackend
from hpc_portal.domain.errors import UseCaseError
from hpc_portal.domain.llm.model import ModelName, ModelPullProgress

HPC_LITELLM_LOG = logging.getLogger("jupyterhub.hpc-litellm")


_HPC_LITELLM_OLLAMA_SOURCE = "hpc-portal-ollama"


class ListLlmModelsUseCase:
    def __init__(
        self,
        *,
        client: LlmClient,
        gateway: LlmManagementGateway,
    ):
        self.client = client
        self.gateway = gateway

    def execute(self) -> tuple[list[dict], str | None]:
        """ユーザー画面に表示する LiteLLM model 一覧を取得する。

        LiteLLM の `/models` はバージョンや設定によって response shape が揺れるため、
        複数の候補フィールドから model ID を取り出して重複を除去する。

        Returns:
            1要素目は `id` と `owned_by` を持つ model dict の list。
            2要素目は取得失敗時のエラーメッセージ。成功時は None。
        """
        if not self.client.enabled():
            return ([], "LiteLLM Admin API が未設定です")
        try:
            data = self.client.request("/models", method="GET")
        except RuntimeError as exc:
            return ([], str(exc))
        models = []
        seen = set()
        for record in self.gateway.model_records(data):
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
                {
                    "id": model_id,
                    "owned_by": str(
                        record.get("owned_by") or record.get("provider") or ""
                    ).strip(),
                }
            )
        models.sort(key=lambda item: item["id"])
        return (models, None)


class RegisterLlmModelUseCase:
    def __init__(
        self,
        *,
        client: LlmClient,
        gateway: LlmManagementGateway,
        model_inventory: ModelInventory,
        ollama_base_url: str,
    ):
        self.client = client
        self.gateway = gateway
        self.model_inventory = model_inventory
        self.ollama_base_url = ollama_base_url

    def execute(self, model: str) -> tuple[dict | None, str | None]:
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
        error = ModelName(model).validation_error()
        if error:
            return (None, error)
        if not self.client.enabled():
            return (None, "LiteLLM Admin API が未設定です")
        with self.gateway.model_lock(model):
            exists, ollama_err = self.model_inventory.has_model(model)
            if ollama_err or not exists:
                return (None, ollama_err or f"Ollamaにモデル {model} がありません")
            supports_tools, capability_err = self.model_inventory.model_supports_tools(
                model
            )
            if capability_err or supports_tools is None:
                return (None, capability_err or "Ollamaモデルの機能を確認できません")
            response, err = self.gateway.model_info()
            if err:
                return (None, err)
            deployments = self.gateway.ollama_deployments(response, model)
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
                return (
                    None,
                    "同名の設定ファイル由来または手動登録されたollama/モデルがあるため、LiteLLM管理画面で確認してください",
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
                    return (None, self.gateway.safe_litellm_error(exc))
                created = True
            verified_response, err = self.gateway.model_info()
            if err:
                return (None, f"登録後の確認に失敗しました: {err}")
            verified = self.gateway.ollama_deployments(verified_response, model)
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
            delete_err = self.gateway.delete_deployments(
                [item["id"] for item in verified_legacy]
            )
            if delete_err:
                return (None, "旧LiteLLMモデルの削除に失敗しました: " + delete_err)
            migrated = len({item["id"] for item in legacy if item["id"]})
            HPC_LITELLM_LOG.info(
                "action=model_sync model=%s backend=%s supports_tools=%s migrated=%d result=ok",
                model,
                target_backend,
                supports_tools,
                migrated,
            )
            return (
                {
                    "state": "registered"
                    if created or migrated
                    else "already_registered",
                    "model": model,
                    "backend": target_backend,
                    "supports_tools": supports_tools,
                    "migrated": migrated,
                    "message": "LiteLLMへ同期しました"
                    if created or migrated
                    else "LiteLLM同期済み",
                },
                None,
            )


class SynchronizeLlmModelsUseCase:
    def __init__(
        self,
        *,
        model_inventory: ModelInventory,
        register_ollama_model: RegisterLlmModelUseCase,
    ):
        self.model_inventory = model_inventory
        self.register_ollama_model = register_ollama_model

    def execute(self) -> tuple[dict | None, str | None]:
        """Ollamaに存在する全モデルをLiteLLMへ同期する。

        Returns:
            ``(モデル別結果と件数, エラー)``。一覧取得失敗時のみ全体エラーを返す。
        """
        model_names, err = self.model_inventory.model_names()
        if err:
            return (None, err)
        results = []
        for model in model_names:
            state, model_err = self.register_ollama_model.execute(model)
            if model_err:
                results.append(
                    {"model": model, "state": "failed", "message": model_err}
                )
            else:
                results.append(state or {"model": model, "state": "failed"})
        failed = sum((item.get("state") == "failed" for item in results))
        changed = sum((item.get("state") == "registered" for item in results))
        HPC_LITELLM_LOG.info(
            "action=models_sync total=%d changed=%d failed=%d result=%s",
            len(results),
            changed,
            failed,
            "partial" if failed else "ok",
        )
        return (
            {
                "total": len(results),
                "changed": changed,
                "failed": failed,
                "results": results,
            },
            None,
        )


class UnregisterLlmModelUseCase:
    def __init__(
        self,
        *,
        client: LlmClient,
        gateway: LlmManagementGateway,
    ):
        self.client = client
        self.gateway = gateway

    def execute(self, model: str) -> str | None:
        """Ollamaモデルに対応するLiteLLMのDBモデルを削除する。

        Args:
            model: Ollamaから削除するモデル名。

        Returns:
            正常または該当モデルなしならNone、失敗時はエラーメッセージ。
        """
        model = str(model or "").strip()
        error = ModelName(model).validation_error()
        if error:
            return error
        if not self.client.enabled():
            return "LiteLLM Admin API が未設定です"
        with self.gateway.model_lock(model):
            try:
                response = self.client.request("/v1/model/info", method="GET")
            except RuntimeError as exc:
                return self.gateway.safe_litellm_error(exc)
            deployments = self.gateway.ollama_deployments(response, model)
            deployment_ids = [
                item["id"]
                for item in deployments
                if item["portal_managed"] and item["id"]
            ]
            delete_err = self.gateway.delete_deployments(deployment_ids)
            if delete_err:
                return delete_err
            if deployment_ids:
                HPC_LITELLM_LOG.info(
                    "action=model_delete model=%s deployments=%d result=ok",
                    model,
                    len(set(deployment_ids)),
                )
            return None


HPC_OLLAMA_LOG = logging.getLogger("jupyterhub.hpc-ollama")


class RegisterInstalledModelUseCase:
    def __init__(
        self,
        *,
        backend: OllamaBackend,
        register_model: RegisterLlmModelUseCase,
    ):
        self.backend = backend
        self.register_model = register_model

    async def execute(self, request):
        model = request.model
        exists, err = await asyncio.to_thread(self.backend.has_model, model)
        if err or not exists:
            raise UseCaseError(err or "Ollamaにモデルがありません")
        registration, err = await asyncio.to_thread(self.register_model.execute, model)
        if err:
            raise UseCaseError(err)
        return {"ok": True, "data": registration}


class SynchronizeInstalledModelsUseCase:
    def __init__(
        self,
        *,
        synchronize_models: SynchronizeLlmModelsUseCase,
    ):
        self.synchronize_models = synchronize_models

    async def execute(self, request):
        result, err = await asyncio.to_thread(self.synchronize_models.execute)
        if err:
            raise UseCaseError(err)
        return {"ok": True, "data": result}


class DeleteInstalledModelUseCase:
    def __init__(
        self,
        *,
        backend: OllamaBackend,
        unregister_model: UnregisterLlmModelUseCase,
        register_model: RegisterLlmModelUseCase,
    ):
        self.backend = backend
        self.unregister_model = unregister_model
        self.register_model = register_model

    async def execute(self, request):
        model = request.model
        tags, err = await asyncio.to_thread(self.backend.command, "tags")
        if err:
            raise UseCaseError(err)
        exists = any(
            (
                isinstance(item, dict) and str(item.get("name") or "") == model
                for item in (tags or {}).get("models", []) or []
            )
        )
        litellm_err = await asyncio.to_thread(self.unregister_model.execute, model)
        if litellm_err:
            raise UseCaseError("LiteLLMモデルを削除できませんでした: " + litellm_err)
        if exists:
            _data, err = await asyncio.to_thread(self.backend.command, "delete", model)
            if err:
                await asyncio.to_thread(self.register_model.execute, model)
                raise UseCaseError(err)
        litellm_err = await asyncio.to_thread(self.unregister_model.execute, model)
        if litellm_err:
            raise UseCaseError("LiteLLMモデルを削除できませんでした: " + litellm_err)
        return {"ok": True, "data": {"model": model, "deleted": True}}


class PullLlmModelUseCase:
    def __init__(
        self,
        *,
        backend: OllamaBackend,
        start_registration_watcher: Callable[[str], None],
    ):
        self.backend = backend
        self.start_registration_watcher = start_registration_watcher

    async def execute(self, request):
        model = request.model
        data, err = await asyncio.to_thread(self.backend.command, "pull", model)
        if err:
            raise UseCaseError(err)
        self.start_registration_watcher(model)
        return {"ok": True, "data": data}


class CancelLlmModelPullUseCase:
    def __init__(
        self,
        *,
        backend: OllamaBackend,
    ):
        self.backend = backend

    async def execute(self, request):
        model = request.model
        data, err = await asyncio.to_thread(self.backend.command, "pull-cancel", model)
        if err:
            raise UseCaseError(err)
        return {"ok": True, "data": data}


class GetLlmModelPullStatusUseCase:
    def __init__(
        self,
        *,
        backend: OllamaBackend,
        register_model: RegisterLlmModelUseCase,
    ):
        self.backend = backend
        self.register_model = register_model

    async def execute(self, request):
        model = request.model
        data, err = await asyncio.to_thread(self.backend.pull_progress, model or None)
        if not err and data and (data.get("state") == "completed"):
            completed_model = str(data.get("model") or model).strip()
            registration, registration_err = await asyncio.to_thread(
                self.register_model.execute, completed_model
            )
            data["litellm_registration"] = registration or {
                "state": "failed",
                "model": completed_model,
                "message": registration_err or "LiteLLM登録に失敗しました",
            }
        if err:
            raise UseCaseError(err)
        return {"ok": True, "data": data}


class WatchLlmModelPullUseCase:
    def __init__(
        self,
        *,
        backend: OllamaBackend,
        gateway: LlmManagementGateway,
        register_model: RegisterLlmModelUseCase,
        registration_tasks: dict[str, asyncio.Task[None]],
    ):
        self.backend = backend
        self.gateway = gateway
        self.register_model = register_model
        self.registration_tasks = registration_tasks

    async def execute(self, model: str) -> None:
        """Ollama pull完了を監視してLiteLLMへ登録する。

        Args:
            model: pullを開始したOllamaモデル名。
        """
        idle_count = 0
        try:
            while True:
                progress, err = await asyncio.to_thread(
                    self.backend.pull_progress, model
                )
                if err:
                    HPC_OLLAMA_LOG.warning(
                        "action=model_register model=%s result=failed error=%s",
                        model,
                        self.gateway.safe_litellm_error(err),
                    )
                    return
                state = str((progress or {}).get("state") or "")
                if ModelPullProgress(state).completed:
                    _registration, registration_err = await asyncio.to_thread(
                        self.register_model.execute, model
                    )
                    if registration_err:
                        HPC_OLLAMA_LOG.warning(
                            "action=model_register model=%s result=failed error=%s",
                            model,
                            self.gateway.safe_litellm_error(registration_err),
                        )
                    return
                if ModelPullProgress(state).terminal:
                    return
                idle_count = idle_count + 1 if state == "idle" else 0
                if idle_count >= 20:
                    HPC_OLLAMA_LOG.warning(
                        "action=model_register model=%s result=failed error=pull_status_timeout",
                        model,
                    )
                    return
                await asyncio.sleep(1.5)
        except Exception as exc:
            HPC_OLLAMA_LOG.warning(
                "action=model_register model=%s result=failed error=%s",
                model,
                self.gateway.safe_litellm_error(exc),
            )
        finally:
            current = asyncio.current_task()
            if self.registration_tasks.get(model) is current:
                self.registration_tasks.pop(model, None)
