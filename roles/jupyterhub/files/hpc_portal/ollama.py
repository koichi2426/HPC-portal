"""共有Ollamaの起動、停止、モデル管理を提供する。"""

import json
import re

from .common import (
    HPC_OLLAMA_ALLOWED_CPUS,
    HPC_OLLAMA_ALLOWED_CONTEXT_LENGTHS,
    HPC_OLLAMA_ALLOWED_KEEP_ALIVE,
    HPC_OLLAMA_ALLOWED_KV_CACHE_TYPES,
    HPC_OLLAMA_ALLOWED_MAX_LOADED_MODELS,
    HPC_OLLAMA_ALLOWED_MAX_QUEUE,
    HPC_OLLAMA_ALLOWED_MEMORY,
    HPC_OLLAMA_ALLOWED_PARALLEL,
    HPC_OLLAMA_DEFAULT_CPUS,
    HPC_OLLAMA_DEFAULT_CONTEXT_LENGTH,
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
    c,
)
from .users import _hpc_run_cmd

_HPC_OLLAMA_MODEL_RE = re.compile(r"^[A-Za-z0-9_.:/-]{1,128}$")
HPC_OLLAMA_GPUS = "1"


def _hpc_normalize_ollama_choice(
    value: str | None, default: str, allowed: tuple[str, ...], label: str
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


def _hpc_validate_ollama_resources(
    cpus: str | None, memory: str | None
) -> tuple[str | None, str | None, str | None]:
    """共有Ollamaへ割り当てるCPUとメモリを検証する。

    Args:
        cpus: 要求CPU数。
        memory: Slurm形式の要求メモリ。

    Returns:
        ``(正規化CPU, 正規化メモリ, エラー)``。
    """
    c = str(cpus or HPC_OLLAMA_DEFAULT_CPUS).strip()
    m = str(memory or HPC_OLLAMA_DEFAULT_MEMORY).strip().upper()
    if c not in HPC_OLLAMA_ALLOWED_CPUS:
        return None, None, f"CPU は {', '.join(HPC_OLLAMA_ALLOWED_CPUS)} から選択してください"
    if m not in HPC_OLLAMA_ALLOWED_MEMORY:
        return None, None, f"Memory は {', '.join(HPC_OLLAMA_ALLOWED_MEMORY)} から選択してください"
    return c, m, None


def _hpc_validate_ollama_start_settings(
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
    normalized_cpus, normalized_memory, error = _hpc_validate_ollama_resources(
        cpus, memory
    )
    if error:
        return None, error
    choices = (
        (
            "parallel",
            parallel,
            HPC_OLLAMA_DEFAULT_PARALLEL,
            HPC_OLLAMA_ALLOWED_PARALLEL,
            "同時処理数",
        ),
        (
            "max_loaded_models",
            max_loaded_models,
            HPC_OLLAMA_DEFAULT_MAX_LOADED_MODELS,
            HPC_OLLAMA_ALLOWED_MAX_LOADED_MODELS,
            "同時ロードモデル数",
        ),
        (
            "context_length",
            context_length,
            HPC_OLLAMA_DEFAULT_CONTEXT_LENGTH,
            HPC_OLLAMA_ALLOWED_CONTEXT_LENGTHS,
            "コンテキスト長",
        ),
        (
            "kv_cache_type",
            kv_cache_type,
            HPC_OLLAMA_DEFAULT_KV_CACHE_TYPE,
            HPC_OLLAMA_ALLOWED_KV_CACHE_TYPES,
            "KVキャッシュ",
        ),
        (
            "keep_alive",
            keep_alive,
            HPC_OLLAMA_DEFAULT_KEEP_ALIVE,
            HPC_OLLAMA_ALLOWED_KEEP_ALIVE,
            "モデル保持時間",
        ),
        (
            "max_queue",
            max_queue,
            HPC_OLLAMA_DEFAULT_MAX_QUEUE,
            HPC_OLLAMA_ALLOWED_MAX_QUEUE,
            "最大待機数",
        ),
    )
    settings = {
        "cpus": normalized_cpus or HPC_OLLAMA_DEFAULT_CPUS,
        "memory": normalized_memory or HPC_OLLAMA_DEFAULT_MEMORY,
    }
    for key, value, default, allowed, label in choices:
        normalized, error = _hpc_normalize_ollama_choice(
            value, default, allowed, label
        )
        if error:
            return None, error
        settings[key] = normalized or default
    enabled = (
        HPC_OLLAMA_DEFAULT_FLASH_ATTENTION
        if flash_attention is None
        else flash_attention
    )
    if not isinstance(enabled, bool):
        return None, "Flash Attention は真偽値で指定してください"
    settings["flash_attention"] = "1" if enabled else "0"
    return settings, None


def _hpc_ollama_cmd(
    action: str,
    model: str | None = None,
    cpus: str | None = None,
    memory: str | None = None,
    parallel: str | None = None,
    max_loaded_models: str | None = None,
    context_length: str | None = None,
    kv_cache_type: str | None = None,
    keep_alive: str | None = None,
    max_queue: str | None = None,
    flash_attention: bool | None = None,
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
    cmd = ["/usr/local/sbin/hpc-ollama", action]
    start_settings = None
    if action == "start":
        start_settings, err = _hpc_validate_ollama_start_settings(
            cpus,
            memory,
            parallel,
            max_loaded_models,
            context_length,
            kv_cache_type,
            keep_alive,
            max_queue,
            flash_attention,
        )
        if err:
            return None, err
        option_names = {
            "cpus": "--cpus",
            "memory": "--memory",
            "parallel": "--parallel",
            "max_loaded_models": "--max-loaded-models",
            "context_length": "--context-length",
            "kv_cache_type": "--kv-cache-type",
            "keep_alive": "--keep-alive",
            "max_queue": "--max-queue",
            "flash_attention": "--flash-attention",
        }
        for key, option in option_names.items():
            cmd.extend([option, start_settings[key]])
    if model:
        if not _HPC_OLLAMA_MODEL_RE.fullmatch(model):
            return None, "モデル名に使用できない文字が含まれています"
        cmd.append(model)
    result = _hpc_run_cmd(cmd)
    body = (result.stdout or "").strip()
    if result.returncode != 0:
        return None, (result.stderr or result.stdout or "hpc-ollama failed").strip()
    if action == "status" and body:
        try:
            return json.loads(body), None
        except json.JSONDecodeError:
            return {"raw": body}, None
    if action in {"tags", "show", "ps", "pull-status", "pull-cancel"} and body:
        try:
            return json.loads(body), None
        except json.JSONDecodeError:
            return {"raw": body}, None
    if action == "start" and body:
        try:
            data = json.loads(body)
            for key, value in (start_settings or {}).items():
                data.setdefault(key, value)
            data.setdefault("gpus", HPC_OLLAMA_GPUS)
            return data, None
        except json.JSONDecodeError:
            return {
                "ok": True,
                "output": body,
                **(start_settings or {}),
                "gpus": HPC_OLLAMA_GPUS,
            }, None
    return {"ok": True, "output": body}, None


def _hpc_ollama_pull_progress(model: str | None = None) -> tuple[dict | None, str | None]:
    """バックグラウンドpullの最終進捗を表示用データに変換する。

    Args:
        model: 状態を確認するモデル名。省略時は実行中モデルを使う。

    Returns:
        ``(進捗情報, エラー)``。
    """
    data, err = _hpc_ollama_cmd("pull-status", model)
    if err or data is None:
        return None, err or "pull 状態を取得できません"
    active = bool(data.get("active"))
    active_model = str(data.get("active_model") or "")
    selected_model = str(data.get("model") or model or active_model or "")
    last = str(data.get("last") or "")
    record = {}
    if last:
        try:
            parsed = json.loads(last)
            if isinstance(parsed, dict):
                record = parsed
        except json.JSONDecodeError:
            record = {"error": last[:300]}
    total = record.get("total")
    completed = record.get("completed")
    try:
        total = max(0, int(total)) if total is not None else None
    except (TypeError, ValueError):
        total = None
    try:
        completed = max(0, int(completed)) if completed is not None else None
    except (TypeError, ValueError):
        completed = None
    state = "idle"
    if active:
        state = "pulling" if not model or active_model == model else "busy"
    result = str(data.get("result") or "")
    if not active and result == "cancelled":
        state = "cancelled"
    elif not active and result == "cancelled_cleanup_failed":
        state = "cancelled_cleanup_failed"
    elif result not in ("", "0"):
        state = "failed"
    elif str(record.get("status") or "").lower() == "success" or result == "0":
        state = "completed"
    return {
        "state": state,
        "model": selected_model,
        "active_model": active_model,
        "status": str(record.get("status") or ""),
        "completed": completed,
        "total": total,
        "error": str(record.get("error") or "")[:300],
    }, None


def _hpc_ollama_has_model(model: str) -> tuple[bool, str | None]:
    """指定モデルがOllamaに存在するか確認する。

    Args:
        model: 確認するOllamaモデル名。

    Returns:
        ``(存在するか, エラー)``。
    """
    if not _HPC_OLLAMA_MODEL_RE.fullmatch(str(model or "")):
        return False, "モデル名に使用できない文字が含まれています"
    tags, err = _hpc_ollama_cmd("tags")
    if err:
        return False, err
    for item in (tags or {}).get("models", []) or []:
        if isinstance(item, dict) and str(item.get("name") or "") == model:
            return True, None
    return False, f"Ollamaにモデル {model} がありません"


def _hpc_ollama_model_names() -> tuple[list[str], str | None]:
    """Ollamaへインストール済みのモデル名を取得する。

    Returns:
        ``(重複を除いて並べ替えたモデル名, エラー)``。
    """
    tags, err = _hpc_ollama_cmd("tags")
    if err:
        return [], err
    names = {
        str(item.get("name") or "").strip()
        for item in (tags or {}).get("models", []) or []
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    }
    return sorted(names), None


def _hpc_ollama_model_supports_tools(model: str) -> tuple[bool | None, str | None]:
    """Ollamaモデルがツール呼び出しに対応するか確認する。

    Args:
        model: 確認するOllamaモデル名。

    Returns:
        ``(tools capabilityの有無, エラー)``。取得失敗時の値はNone。
    """
    data, err = _hpc_ollama_cmd("show", model)
    if err:
        return None, err
    capabilities = (data or {}).get("capabilities", [])
    if not isinstance(capabilities, list):
        capabilities = []
    return "tools" in {str(item).strip().lower() for item in capabilities}, None


def _hpc_shared_ollama_detail_context(user=None) -> dict:
    """Ollama 詳細表示用コンテキストを作成する。

    JupyterHub の template_vars は callable を `value(user)` として呼び出すため、
    user 引数を受け取れる形にしている。現在の実装では user ごとの差分はなく、
    管理者向け shared service の状態を同じ形式で返す。

    Args:
        user: JupyterHubが渡すログインユーザー。現在は未使用。

    Returns:
        app_detail.htmlとhome.htmlで使うOllamaの表示情報。
    """
    status, status_err = _hpc_ollama_cmd("status")
    status = status or {}
    running = bool(status.get("running"))
    api = bool(status.get("api"))
    # Slurmジョブの起動直後はAPIがまだ待受を開始していないため、
    # 接続失敗をモデル取得エラーとして画面へ出さない。
    tags, tags_err = _hpc_ollama_cmd("tags") if api else (None, None)
    models = []
    if tags and isinstance(tags, dict):
        for item in tags.get("models", []) or []:
            if isinstance(item, dict):
                models.append({
                    "name": item.get("name", ""),
                    "size": item.get("size", ""),
                    "modified_at": item.get("modified_at", ""),
                })
    running_version = str(status.get("version") or "").removeprefix("v")
    target_version = HPC_OLLAMA_VERSION.removeprefix("v")
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
            "gpu": 1,
            "gpu_label": "1 GPU",
            "runtime": HPC_OLLAMA_RUNTIME,
            "hours": "無制限",
        },
        "port": HPC_OLLAMA_PORT,
        "models_dir": HPC_OLLAMA_MODELS_DIR,
        "api": api,
        "version": running_version,
        "target_version": target_version,
        "update_available": bool(
            running_version and target_version and running_version != target_version
        ),
        "ollama_settings": {
            "parallel": running_setting(
                "parallel", HPC_OLLAMA_DEFAULT_PARALLEL
            ) or "不明",
            "max_loaded_models": running_setting(
                "max_loaded_models", HPC_OLLAMA_DEFAULT_MAX_LOADED_MODELS
            ) or "不明",
            "context_length": context_length,
            "context_length_label": context_length_label,
            "kv_cache_type": running_setting(
                "kv_cache_type", HPC_OLLAMA_DEFAULT_KV_CACHE_TYPE
            ) or "不明",
            "keep_alive": keep_alive,
            "keep_alive_label": keep_alive_label,
            "max_queue": running_setting(
                "max_queue", HPC_OLLAMA_DEFAULT_MAX_QUEUE
            ) or "不明",
            "flash_attention": {
                "value": flash_attention,
                "label": {"1": "ON", "0": "OFF"}.get(
                    flash_attention, "不明"
                ),
            },
        },
        "models": models,
        "status_error": status_err or tags_err or "",
    }


c.JupyterHub.template_vars.update({
    "hpc_shared_ollama_detail": _hpc_shared_ollama_detail_context,
    "hpc_ollama_version": HPC_OLLAMA_VERSION.removeprefix("v"),
})
