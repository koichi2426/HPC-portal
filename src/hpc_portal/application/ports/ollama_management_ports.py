"""Ollama管理に必要な操作の契約。"""

from typing import Protocol

from hpc_portal.application.ports.llm_management_ports import ModelInventory


class OllamaBackend(ModelInventory, Protocol):
    def command(
        self,
        action: str,
        model=None,
        cpus=None,
        memory=None,
        parallel=None,
        max_loaded_models=None,
        context_length=None,
        kv_cache_type=None,
        keep_alive=None,
        max_queue=None,
        flash_attention=None,
    ) -> tuple[dict | None, str | None]: ...
    def pull_progress(self, model=None) -> tuple[dict | None, str | None]: ...
