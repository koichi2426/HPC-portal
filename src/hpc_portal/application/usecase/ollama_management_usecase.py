"""Ollama操作と、モデルのLiteLLM登録・復旧の手順。"""

import asyncio
import logging

from hpc_portal.application.ports.ollama_management_ports import OllamaBackend
from hpc_portal.domain.errors import UseCaseError

HPC_OLLAMA_LOG = logging.getLogger("jupyterhub.hpc-ollama")


def raise_operation_error(status, message):
    raise UseCaseError(message)


class OllamaManagementUseCase:
    def __init__(self, client: OllamaBackend, llm):
        self.client = client
        self.llm = llm
        self.registration_tasks = {}

    async def execute(self, request):
        action = request.action
        if action == "ollama_register_model":
            model = request.model
            exists, err = await asyncio.to_thread(self.client.has_model, model)
            if err or not exists:
                raise_operation_error(400, err or "Ollamaにモデルがありません")
            registration, err = await asyncio.to_thread(
                self.llm.register_ollama_model, model
            )
            if err:
                raise_operation_error(400, err)
            return {"ok": True, "data": registration}
        if action == "ollama_sync_models":
            result, err = await asyncio.to_thread(self.llm.sync_ollama_models)
            if err:
                raise_operation_error(400, err)
            return {"ok": True, "data": result}
        if action == "ollama_delete":
            model = request.model
            tags, err = await asyncio.to_thread(self.client.command, "tags")
            if err:
                raise_operation_error(400, err)
            exists = any(
                isinstance(item, dict) and str(item.get("name") or "") == model
                for item in (tags or {}).get("models", []) or []
            )
            litellm_err = await asyncio.to_thread(self.llm.delete_ollama_model, model)
            if litellm_err:
                raise_operation_error(
                    400, "LiteLLMモデルを削除できませんでした: " + litellm_err
                )
            if exists:
                _data, err = await asyncio.to_thread(
                    self.client.command, "delete", model
                )
                if err:
                    await asyncio.to_thread(self.llm.register_ollama_model, model)
                    raise_operation_error(400, err)
            # pull完了との競合で再登録された場合も、Ollama削除後にもう一度回収する。
            litellm_err = await asyncio.to_thread(self.llm.delete_ollama_model, model)
            if litellm_err:
                raise_operation_error(
                    400, "LiteLLMモデルを削除できませんでした: " + litellm_err
                )
            return {"ok": True, "data": {"model": model, "deleted": True}}
        if action in {
            "ollama_start",
            "ollama_stop",
            "ollama_update_check",
            "ollama_update",
            "ollama_status",
            "ollama_tags",
            "ollama_pull",
            "ollama_pull_cancel",
            "ollama_pull_status",
        }:
            mapping = {
                "ollama_start": "start",
                "ollama_stop": "stop",
                "ollama_update_check": "update-check",
                "ollama_update": "update",
                "ollama_status": "status",
                "ollama_tags": "tags",
                "ollama_pull": "pull",
                "ollama_pull_cancel": "pull-cancel",
                "ollama_pull_status": "pull-status",
            }
            model = request.model
            if action == "ollama_pull_status":
                data, err = await asyncio.to_thread(
                    self.client.pull_progress, model or None
                )
                if not err and data and data.get("state") == "completed":
                    completed_model = str(data.get("model") or model).strip()
                    registration, registration_err = await asyncio.to_thread(
                        self.llm.register_ollama_model, completed_model
                    )
                    data["litellm_registration"] = registration or {
                        "state": "failed",
                        "model": completed_model,
                        "message": registration_err or "LiteLLM登録に失敗しました",
                    }
            else:
                data, err = await asyncio.to_thread(
                    self.client.command,
                    mapping[action],
                    model if action in {"ollama_pull", "ollama_pull_cancel"} else None,
                    request.cpus if action == "ollama_start" else None,
                    request.memory if action == "ollama_start" else None,
                    request.parallel if action == "ollama_start" else None,
                    request.max_loaded_models if action == "ollama_start" else None,
                    request.context_length if action == "ollama_start" else None,
                    request.kv_cache_type if action == "ollama_start" else None,
                    request.keep_alive if action == "ollama_start" else None,
                    request.max_queue if action == "ollama_start" else None,
                    request.flash_attention if action == "ollama_start" else None,
                )
            if err:
                raise_operation_error(400, err)
            if action == "ollama_pull":
                self.start_registration_watcher(model)
            return {"ok": True, "data": data}
        raise_operation_error(400, "不明な action です")

    async def watch_pull_and_register(self, model: str) -> None:
        """Ollama pull完了を監視してLiteLLMへ登録する。

        Args:
            model: pullを開始したOllamaモデル名。
        """
        idle_count = 0
        try:
            while True:
                progress, err = await asyncio.to_thread(
                    self.client.pull_progress, model
                )
                if err:
                    HPC_OLLAMA_LOG.warning(
                        "action=model_register model=%s result=failed error=%s",
                        model,
                        self.llm.safe_litellm_error(err),
                    )
                    return
                state = str((progress or {}).get("state") or "")
                if state == "completed":
                    _registration, registration_err = await asyncio.to_thread(
                        self.llm.register_ollama_model, model
                    )
                    if registration_err:
                        HPC_OLLAMA_LOG.warning(
                            "action=model_register model=%s result=failed error=%s",
                            model,
                            self.llm.safe_litellm_error(registration_err),
                        )
                    return
                if state in {"failed", "busy", "cancelled", "cancelled_cleanup_failed"}:
                    return
                idle_count = idle_count + 1 if state == "idle" else 0
                if idle_count >= 20:
                    HPC_OLLAMA_LOG.warning(
                        "action=model_register model=%s result=failed error=pull_status_timeout",
                        model,
                    )
                    return
                await asyncio.sleep(1.5)
        except Exception as exc:  # noqa: BLE001
            HPC_OLLAMA_LOG.warning(
                "action=model_register model=%s result=failed error=%s",
                model,
                self.llm.safe_litellm_error(exc),
            )
        finally:
            current = asyncio.current_task()
            if self.registration_tasks.get(model) is current:
                self.registration_tasks.pop(model, None)

    def start_registration_watcher(self, model: str) -> None:
        """モデル登録監視タスクを重複なく開始する。

        Args:
            model: pullを開始したOllamaモデル名。
        """
        existing = self.registration_tasks.get(model)
        if existing and not existing.done():
            return
        self.registration_tasks[model] = asyncio.create_task(
            self.watch_pull_and_register(model)
        )
