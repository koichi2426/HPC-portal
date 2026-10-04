"""OllamaのCPU・メモリ・実行設定の許可ルール。"""


class OllamaResourcePolicy:
    def __init__(self, settings):
        self.settings = settings

    def normalize_ollama_choice(
        self, value: str | None, default: str, allowed: tuple[str, ...], label: str
    ) -> tuple[str | None, str | None]:
        """Ollama起動設定を許可値へ正規化する。

        Args:
            value: 入力された設定値。
            default: 未入力時の既定値。
            allowed: 選択を許可する値。
            label: エラーメッセージへ表示する設定名。

        Returns:
            ``(正規化値, エラー)``。
        """
        normalized = str(value if value not in (None, "") else default).strip()
        if normalized not in allowed:
            return None, f"{label} は {', '.join(allowed)} から選択してください"
        return normalized, None

    def validate_ollama_resources(
        self, cpus: str | None, memory: str | None
    ) -> tuple[str | None, str | None, str | None]:
        """共有Ollamaへ割り当てるCPUとメモリを検証する。

        Args:
            cpus: 要求CPU数。
            memory: Slurm形式の要求メモリ。

        Returns:
            ``(正規化CPU, 正規化メモリ, エラー)``。
        """
        c = str(cpus or self.settings.HPC_OLLAMA_DEFAULT_CPUS).strip()
        m = str(memory or self.settings.HPC_OLLAMA_DEFAULT_MEMORY).strip().upper()
        if c not in self.settings.HPC_OLLAMA_ALLOWED_CPUS:
            return (
                None,
                None,
                f"CPU は {', '.join(self.settings.HPC_OLLAMA_ALLOWED_CPUS)} から選択してください",
            )
        if m not in self.settings.HPC_OLLAMA_ALLOWED_MEMORY:
            return (
                None,
                None,
                f"Memory は {', '.join(self.settings.HPC_OLLAMA_ALLOWED_MEMORY)} から選択してください",
            )
        return c, m, None

    def validate_ollama_start_settings(
        self,
        cpus: str | None = None,
        memory: str | None = None,
        parallel: str | None = None,
        max_loaded_models: str | None = None,
        context_length: str | None = None,
        kv_cache_type: str | None = None,
        keep_alive: str | None = None,
        max_queue: str | None = None,
        flash_attention: bool | None = None,
    ) -> tuple[dict[str, str] | None, str | None]:
        """共有Ollamaの全起動設定を検証する。

        Args:
            cpus: Slurmへ要求するCPU数。
            memory: Slurmへ要求するメモリ。
            parallel: 1モデルあたりの同時処理数。
            max_loaded_models: 同時ロードモデル数の上限。
            context_length: 既定コンテキスト長。
            kv_cache_type: KVキャッシュ量子化形式。
            keep_alive: モデルをメモリへ保持する時間。
            max_queue: 待機リクエスト数の上限。
            flash_attention: Flash Attentionの有効状態。

        Returns:
            ``(正規化済み設定, エラー)``。
        """
        normalized_cpus, normalized_memory, error = self.validate_ollama_resources(
            cpus, memory
        )
        if error:
            return None, error
        choices = (
            (
                "parallel",
                parallel,
                self.settings.HPC_OLLAMA_DEFAULT_PARALLEL,
                self.settings.HPC_OLLAMA_ALLOWED_PARALLEL,
                "同時処理数",
            ),
            (
                "max_loaded_models",
                max_loaded_models,
                self.settings.HPC_OLLAMA_DEFAULT_MAX_LOADED_MODELS,
                self.settings.HPC_OLLAMA_ALLOWED_MAX_LOADED_MODELS,
                "同時ロードモデル数",
            ),
            (
                "context_length",
                context_length,
                self.settings.HPC_OLLAMA_DEFAULT_CONTEXT_LENGTH,
                self.settings.HPC_OLLAMA_ALLOWED_CONTEXT_LENGTHS,
                "コンテキスト長",
            ),
            (
                "kv_cache_type",
                kv_cache_type,
                self.settings.HPC_OLLAMA_DEFAULT_KV_CACHE_TYPE,
                self.settings.HPC_OLLAMA_ALLOWED_KV_CACHE_TYPES,
                "KVキャッシュ",
            ),
            (
                "keep_alive",
                keep_alive,
                self.settings.HPC_OLLAMA_DEFAULT_KEEP_ALIVE,
                self.settings.HPC_OLLAMA_ALLOWED_KEEP_ALIVE,
                "モデル保持時間",
            ),
            (
                "max_queue",
                max_queue,
                self.settings.HPC_OLLAMA_DEFAULT_MAX_QUEUE,
                self.settings.HPC_OLLAMA_ALLOWED_MAX_QUEUE,
                "最大待機数",
            ),
        )
        settings = {
            "cpus": normalized_cpus or self.settings.HPC_OLLAMA_DEFAULT_CPUS,
            "memory": normalized_memory or self.settings.HPC_OLLAMA_DEFAULT_MEMORY,
        }
        for key, value, default, allowed, label in choices:
            normalized, error = self.normalize_ollama_choice(
                value, default, allowed, label
            )
            if error:
                return None, error
            settings[key] = normalized or default
        enabled = (
            self.settings.HPC_OLLAMA_DEFAULT_FLASH_ATTENTION
            if flash_attention is None
            else flash_attention
        )
        if not isinstance(enabled, bool):
            return None, "Flash Attention は真偽値で指定してください"
        settings["flash_attention"] = "1" if enabled else "0"
        return settings, None
