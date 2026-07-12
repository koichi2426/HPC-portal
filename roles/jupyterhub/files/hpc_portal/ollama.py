"""共有Ollamaの起動、停止、モデル管理を提供する。"""

from .common import (
    HPC_OLLAMA_ALLOWED_CPUS,
    HPC_OLLAMA_ALLOWED_MEMORY,
    HPC_OLLAMA_DEFAULT_CPUS,
    HPC_OLLAMA_DEFAULT_MEMORY,
    HPC_OLLAMA_MODELS_DIR,
    HPC_OLLAMA_PORT,
    HPC_OLLAMA_RUNTIME,
    c,
    json,
    re,
)
from .users import _hpc_run_cmd

_HPC_OLLAMA_MODEL_RE = re.compile(r"^[A-Za-z0-9_.:/-]{1,128}$")
HPC_OLLAMA_GPUS = "1"

def _hpc_validate_ollama_resources(cpus: str | None, memory: str | None) -> tuple[str | None, str | None, str | None]:
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


def _hpc_ollama_cmd(action: str, model: str | None = None, cpus: str | None = None, memory: str | None = None) -> tuple[dict | None, str | None]:
    """hpc-ollama管理コマンドを実行する。

    Args:
        action: start、stop、status、pull、deleteなどの操作名。
        model: pullまたはdelete対象のモデル名。
        cpus: start時のCPU数。
        memory: start時の要求メモリ。

    Returns:
        ``(JSON結果, エラー)``。
    """
    cmd = ["/usr/local/sbin/hpc-ollama", action]
    if action == "start":
        c, m, err = _hpc_validate_ollama_resources(cpus, memory)
        if err:
            return None, err
        cmd.extend(["--cpus", c or HPC_OLLAMA_DEFAULT_CPUS, "--memory", m or HPC_OLLAMA_DEFAULT_MEMORY])
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
    if action in {"tags", "ps", "pull-status"} and body:
        try:
            return json.loads(body), None
        except json.JSONDecodeError:
            return {"raw": body}, None
    if action == "start" and body:
        try:
            data = json.loads(body)
            data.setdefault("cpus", cpus or HPC_OLLAMA_DEFAULT_CPUS)
            data.setdefault("memory", memory or HPC_OLLAMA_DEFAULT_MEMORY)
            data.setdefault("gpus", HPC_OLLAMA_GPUS)
            return data, None
        except json.JSONDecodeError:
            return {"ok": True, "output": body, "cpus": cpus or HPC_OLLAMA_DEFAULT_CPUS, "memory": memory or HPC_OLLAMA_DEFAULT_MEMORY, "gpus": HPC_OLLAMA_GPUS}, None
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
    elif str(data.get("result") or "") not in ("", "0"):
        state = "failed"
    elif str(record.get("status") or "").lower() == "success" or str(data.get("result") or "") == "0":
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
    tags, tags_err = _hpc_ollama_cmd("tags")
    status = status or {}
    models = []
    if tags and isinstance(tags, dict):
        for item in tags.get("models", []) or []:
            if isinstance(item, dict):
                models.append({
                    "name": item.get("name", ""),
                    "size": item.get("size", ""),
                    "modified_at": item.get("modified_at", ""),
                })
    running = bool(status.get("running"))
    api = bool(status.get("api"))
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
        "models": models,
        "status_error": status_err or tags_err or "",
    }


c.JupyterHub.template_vars.update({
    "hpc_shared_ollama_detail": _hpc_shared_ollama_detail_context,
})


