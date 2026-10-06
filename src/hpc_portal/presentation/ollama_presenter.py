from hpc_portal.bootstrap.container import get_container
from hpc_portal.infrastructure.config.settings import (
    HPC_OLLAMA_DEFAULT_CONTEXT_LENGTH,
    HPC_OLLAMA_DEFAULT_CPUS,
    HPC_OLLAMA_DEFAULT_FLASH_ATTENTION,
    HPC_OLLAMA_DEFAULT_KEEP_ALIVE,
    HPC_OLLAMA_DEFAULT_KV_CACHE_TYPE,
    HPC_OLLAMA_DEFAULT_MAX_LOADED_MODELS,
    HPC_OLLAMA_DEFAULT_MAX_QUEUE,
    HPC_OLLAMA_DEFAULT_MEMORY,
    HPC_OLLAMA_DEFAULT_PARALLEL,
    HPC_OLLAMA_MODELS_DIR,
    HPC_OLLAMA_PORT,
    HPC_OLLAMA_RUNTIME,
    HPC_OLLAMA_VERSION,
)


def shared_ollama_detail_context(user=None) -> dict:
    """Ollama 詳細表示用コンテキストを作成する。

    JupyterHub の template_vars は callable を `value(user)` として呼び出すため、
    user 引数を受け取れる形にしている。現在の実装では user ごとの差分はなく、
    管理者向け shared service の状態を同じ形式で返す。

    Args:
        user: JupyterHubが渡すログインユーザー。現在は未使用。

    Returns:
        app_detail.htmlとhome.htmlで使うOllamaの表示情報。
    """
    status, status_err = get_container().ollama.backend.command("status")
    status = status or {}
    running = bool(status.get("running"))
    api = bool(status.get("api"))
    # Slurmジョブの起動直後はAPIがまだ待受を開始していないため、
    # 接続失敗をモデル取得エラーとして画面へ出さない。
    tags, tags_err = (
        get_container().ollama.backend.command("tags") if api else (None, None)
    )
    models = []
    if tags and isinstance(tags, dict):
        for item in tags.get("models", []) or []:
            if isinstance(item, dict):
                models.append(
                    {
                        "name": item.get("name", ""),
                        "size": item.get("size", ""),
                        "modified_at": item.get("modified_at", ""),
                    }
                )
    running_version = str(status.get("version") or "").removeprefix("v")
    latest_version = str(status.get("latest_version") or "").removeprefix("v")

    def running_setting(key: str, default: str) -> str:
        """稼働中は実測値だけを、停止中は次回起動の既定値を返す。

        Args:
            key: status JSON内の設定名。
            default: 停止中に表示する次回起動の既定値。

        Returns:
            画面表示へ使用する設定値。
        """
        return str(status.get(key) or "") if running else default

    context_length = running_setting(
        "context_length", HPC_OLLAMA_DEFAULT_CONTEXT_LENGTH
    )
    try:
        context_length_label = f"{int(context_length) // 1024}K"
    except ValueError:
        context_length_label = context_length or "不明"
    keep_alive = running_setting("keep_alive", HPC_OLLAMA_DEFAULT_KEEP_ALIVE)
    keep_alive_label = {
        "5m": "5分",
        "30m": "30分",
        "1h": "1時間",
        "-1": "常時",
    }.get(keep_alive, keep_alive or "不明")
    flash_attention = running_setting(
        "flash_attention", "1" if HPC_OLLAMA_DEFAULT_FLASH_ATTENTION else "0"
    )
    return {
        "shared_ollama": True,
        "server_name": "shared-ollama",
        "app_label": "Ollama",
        "app_choice": "shared-ollama",
        "status": "running" if running else "stopped",
        "status_label": "実行中" if running else "停止中",
        "active": running,
        "pending": None,
        "job_id": status.get("job_ids", ""),
        "job_host": "",
        "job_url": "",
        "allocation": {
            "cpu": status.get("cpus") or HPC_OLLAMA_DEFAULT_CPUS,
            "memory": status.get("memory") or HPC_OLLAMA_DEFAULT_MEMORY,
            "gpu": get_container().ollama.backend.gpu_count(),
            "gpu_label": get_container().ollama.backend.gpu_label(),
            "runtime": HPC_OLLAMA_RUNTIME,
            "hours": "無制限",
        },
        "port": HPC_OLLAMA_PORT,
        "models_dir": HPC_OLLAMA_MODELS_DIR,
        "api": api,
        "version": running_version,
        "bootstrap_version": str(
            status.get("bootstrap_version") or HPC_OLLAMA_VERSION
        ).removeprefix("v"),
        "latest_version": latest_version,
        "update_available": bool(
            running_version and latest_version and running_version != latest_version
        ),
        "update_state": str(status.get("update_state") or ""),
        "update_target_version": str(
            status.get("update_target_version") or ""
        ).removeprefix("v"),
        "update_message": str(status.get("update_message") or ""),
        "update_error": str(status.get("update_error") or ""),
        "ollama_settings": {
            "parallel": running_setting("parallel", HPC_OLLAMA_DEFAULT_PARALLEL)
            or "不明",
            "max_loaded_models": running_setting(
                "max_loaded_models", HPC_OLLAMA_DEFAULT_MAX_LOADED_MODELS
            )
            or "不明",
            "context_length": context_length,
            "context_length_label": context_length_label,
            "kv_cache_type": running_setting(
                "kv_cache_type", HPC_OLLAMA_DEFAULT_KV_CACHE_TYPE
            )
            or "不明",
            "keep_alive": keep_alive,
            "keep_alive_label": keep_alive_label,
            "max_queue": running_setting("max_queue", HPC_OLLAMA_DEFAULT_MAX_QUEUE)
            or "不明",
            "flash_attention": {
                "value": flash_attention,
                "label": {"1": "ON", "0": "OFF"}.get(flash_attention, "不明"),
            },
        },
        "models": models,
        "status_error": status_err or tags_err or "",
    }
