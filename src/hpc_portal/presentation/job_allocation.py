"""ジョブの割当値を、画面表示用の文言・HTMLへ整える。"""

import html


def runtime_hours_label(runtime: str) -> str:
    """Slurm 形式の実行時間 (HH:MM:SS / UNLIMITED) を表示用に短縮する

    Args:
        runtime: 実行時間の設定値。

    Returns:
        画面表示用の実行時間。
    """
    rt = str(runtime or "").strip()
    if rt.upper() in ("UNLIMITED", "INFINITE"):
        return "無制限"
    try:
        parts = rt.split(":")
        if parts:
            return f"{int(parts[0])}h"
    except (TypeError, ValueError):
        pass
    return rt or "—"


def allocation_summary(user_options) -> dict:
    """起動時に要求した Slurm リソース割り当てを表示用 dict にまとめる

    Args:
        user_options: Spawnerへ渡された起動オプション。

    Returns:
        CPU・メモリ・GPU割当の要約。
    """
    uo = user_options or {}
    app_choice = str(uo.get("app_choice", "ubuntu-cli"))
    if app_choice == "open-webui":
        app_label = "Open WebUI"
    elif app_choice in ("ubuntu-cli", "jupyterlab", "jupyter"):
        app_label = "JupyterLab"
    else:
        app_label = app_choice.replace("-", " ").title() or "Application"
    try:
        gpu_n = int(uo.get("gpu", "0") or 0)
    except (TypeError, ValueError):
        gpu_n = 0
    cpu = str(uo.get("nprocs", "—"))
    mem = str(uo.get("memory", "—"))
    runtime = str(uo.get("runtime", "—"))
    hours = runtime_hours_label(runtime)
    gpu_label = f"{gpu_n} GPU" if gpu_n > 0 else "GPU なし"
    line = f"{cpu} vCPU · {mem} RAM · {gpu_label} · {hours}"
    return {
        "app_label": app_label,
        "cpu": cpu,
        "memory": mem,
        "gpu": gpu_n,
        "gpu_label": gpu_label,
        "runtime": runtime,
        "hours": hours,
        "line": line,
    }


def allocation_html(user_options) -> str:
    """割り当てリソースの HTML 行（spawn フォーム・一覧用）

    Args:
        user_options: Spawnerへ渡された起動オプション。

    Returns:
        HTMLエスケープ済みの割当表示。
    """
    a = allocation_summary(user_options)
    return (
        f'<span class="hpc-muted" style="display:block;margin-top:6px;font-size:0.75rem;'
        f'letter-spacing:0.02em;">割り当て: {a["line"]}</span>'
    )


def stop_button_html(server_name: str) -> str:
    """named server 停止ボタン（data-server-name 空 = デフォルト server）

    Args:
        server_name: 対象のnamed server名。

    Returns:
        停止ボタンのHTML。
    """
    sn = html.escape(str(server_name or ""), quote=True)
    return (
        f'<button type="button" class="gx10-stop-btn" data-server-name="{sn}" '
        f'onclick="hpcStopServer(this)" '
        f'title="Slurmジョブを終了し、このアプリを削除します">停止</button>'
    )
