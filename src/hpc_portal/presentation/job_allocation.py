"""ジョブの割当値を、画面表示用の文言・HTMLへ整える。"""

import html


def runtime_hours_label(runtime: str) -> str:
    """Slurmの実行時間を時間単位へ短縮し、UNLIMITEDは無制限と表示する。"""
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
    """起動オプションから、CPU・メモリ・GPU・実行時間の表示情報を作る。"""
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
    """割当リソースの要約を、起動フォーム・一覧向けのHTMLへ整える。"""
    a = allocation_summary(user_options)
    return (
        f'<span class="hpc-muted" style="display:block;margin-top:6px;font-size:0.75rem;'
        f'letter-spacing:0.02em;">割り当て: {a["line"]}</span>'
    )


def stop_button_html(server_name: str) -> str:
    """named serverの停止ボタンを作る。空の名前はデフォルトserverを表す。"""
    sn = html.escape(str(server_name or ""), quote=True)
    return (
        f'<button type="button" class="gx10-stop-btn" data-server-name="{sn}" '
        f'onclick="hpcStopServer(this)" '
        f'title="Slurmジョブを終了し、このアプリを削除します">停止</button>'
    )
