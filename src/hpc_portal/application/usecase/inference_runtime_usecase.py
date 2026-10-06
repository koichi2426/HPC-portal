"""共有推論サーバーの起動・停止・状態確認・更新。"""

from __future__ import annotations

import asyncio

from hpc_portal.application.ports.ollama_management_ports import OllamaBackend
from hpc_portal.domain.errors import UseCaseError


class StartInferenceRuntimeUseCase:
    def __init__(
        self,
        *,
        backend: OllamaBackend,
    ):
        self.backend = backend

    async def execute(self, request):
        """要求されたリソースと推論設定で共有Ollamaを起動する。"""
        data, err = await asyncio.to_thread(
            self.backend.command,
            "start",
            None,
            request.cpus,
            request.memory,
            request.parallel,
            request.max_loaded_models,
            request.context_length,
            request.kv_cache_type,
            request.keep_alive,
            request.max_queue,
            request.flash_attention,
        )
        if err:
            raise UseCaseError(err)
        return {"ok": True, "data": data}


class StopInferenceRuntimeUseCase:
    def __init__(
        self,
        *,
        backend: OllamaBackend,
    ):
        self.backend = backend

    async def execute(self, request):
        """共有Ollamaを停止し、操作結果を返す。"""
        data, err = await asyncio.to_thread(self.backend.command, "stop")
        if err:
            raise UseCaseError(err)
        return {"ok": True, "data": data}


class CheckInferenceUpdateUseCase:
    def __init__(
        self,
        *,
        backend: OllamaBackend,
    ):
        self.backend = backend

    async def execute(self, request):
        """共有Ollamaに適用できるバージョン更新を確認する。"""
        data, err = await asyncio.to_thread(self.backend.command, "update-check")
        if err:
            raise UseCaseError(err)
        return {"ok": True, "data": data}


class UpdateInferenceRuntimeUseCase:
    def __init__(
        self,
        *,
        backend: OllamaBackend,
    ):
        self.backend = backend

    async def execute(self, request):
        """共有Ollamaの更新処理を実行し、結果を返す。"""
        data, err = await asyncio.to_thread(self.backend.command, "update")
        if err:
            raise UseCaseError(err)
        return {"ok": True, "data": data}


class GetInferenceRuntimeUseCase:
    def __init__(
        self,
        *,
        backend: OllamaBackend,
    ):
        self.backend = backend

    async def execute(self, request):
        """共有Ollamaの実行状態を取得する。"""
        data, err = await asyncio.to_thread(self.backend.command, "status")
        if err:
            raise UseCaseError(err)
        return {"ok": True, "data": data}


class ListInstalledModelsUseCase:
    def __init__(
        self,
        *,
        backend: OllamaBackend,
    ):
        self.backend = backend

    async def execute(self, request):
        """共有Ollamaに保存されているモデルを取得する。"""
        data, err = await asyncio.to_thread(self.backend.command, "tags")
        if err:
            raise UseCaseError(err)
        return {"ok": True, "data": data}
