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
    ) -> tuple[dict | None, str | None]:
        """hpc-ollama管理コマンドを実行する。

        Args:
            action: start、stop、status、pull、deleteなどの操作名。
            model: pullまたはdelete対象のモデル名。
            cpus: start時のCPU数。
            memory: start時の要求メモリ。
            parallel: start時の同時処理数。
            max_loaded_models: start時の同時ロードモデル数。
            context_length: start時のコンテキスト長。
            kv_cache_type: start時のKVキャッシュ形式。
            keep_alive: start時のモデル保持時間。
            max_queue: start時の最大待機数。
            flash_attention: start時のFlash Attention設定。

        Returns:
            ``(JSON結果, エラー)``。
        """
        ...

    def pull_progress(self, model=None) -> tuple[dict | None, str | None]:
        """バックグラウンドpullの最終進捗を表示用データに変換する。

        Args:
            model: 状態を確認するモデル名。省略時は実行中モデルを使う。

        Returns:
            ``(進捗情報, エラー)``。
        """
        ...
