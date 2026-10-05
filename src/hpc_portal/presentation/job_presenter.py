"""ポータル上のアプリ表示とアプリ詳細情報を組み立てる。"""

import json
import urllib.request

from jupyterhub.utils import url_escape_path, url_path_join

from hpc_portal.infrastructure.config.settings import (
    HPC_JUPYTER_UBUNTU_VERSION,
    HPC_OPENWEBUI_VERSION,
    HPC_PUBLIC_SCHEME,
)
from hpc_portal.infrastructure.jupyterhub.job_urls import _job_host, _spawner_job_id
from hpc_portal.presentation.job_allocation import (
    allocation_html as allocation_html,
)
from hpc_portal.presentation.job_allocation import (
    allocation_summary as allocation_summary,
)
from hpc_portal.presentation.job_allocation import (
    runtime_hours_label as runtime_hours_label,
)
from hpc_portal.presentation.job_allocation import (
    stop_button_html as stop_button_html,
)


def openwebui_runtime_version(spawner) -> tuple[str | None, str | None]:
    """起動中Open WebUIの公開バージョンAPIをローカル経由で確認する。

    Args:
        spawner: 確認対象のOpen WebUI Spawner。

    Returns:
        ``(バージョン, エラー)``。確認できない場合はバージョンがNone。
    """
    try:
        port = int(getattr(spawner, "port", 0) or 0)
    except (TypeError, ValueError):
        return None, "ポートを確認できません"
    if not 1 <= port <= 65535:
        return None, "ポートを確認できません"
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/version",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=1.0) as response:
            payload = json.loads(response.read(65536).decode("utf-8"))
        version = str(payload.get("version") or "").strip().removeprefix("v")
        if not version:
            return None, "バージョンが空です"
        return version, None
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return None, str(exc)


def server_name_to_path(server_name: str) -> str:
    """URL パス用（空名は __default__）

    Args:
        server_name: 対象のnamed server名。

    Returns:
        URLへ利用できるserver path。
    """
    return str(server_name or "") or "__default__"


def server_name_from_path(path_segment: str) -> str:
    """URLパス要素からnamed server名を復元する。

    Args:
        path_segment: URLエンコード済みのパス要素。

    Returns:
        デコード済みのserver名。
    """
    return "" if path_segment == "__default__" else path_segment


def spawner_job_url(spawner, server_name: str, user) -> str:
    """アプリへ JUMP する公開 URL（home.html と同じ優先順位）

    Args:
        spawner: 対象のSpawner。
        server_name: 対象のnamed server名。
        user: 対象のJupyterHubユーザー。

    Returns:
        ジョブ用公開URL。生成できない場合は空文字列。
    """
    uo = getattr(spawner, "user_options", None) or {}
    is_openwebui = str(uo.get("app_choice", "")) == "open-webui"
    jid = _spawner_job_id(spawner) or getattr(spawner, "job_id", "") or ""
    public_url = getattr(spawner, "public_url", "") or ""
    if is_openwebui and jid:
        return public_url or f"{HPC_PUBLIC_SCHEME}://{_job_host(jid)}/"
    if getattr(spawner, "active", False) and jid:
        srv = getattr(spawner, "server", None)
        base = getattr(srv, "base_url", None) if srv else None
        if base:
            p = str(base)
            if not p.endswith("/"):
                p += "/"
            return f"{HPC_PUBLIC_SCHEME}://{_job_host(jid)}{p}"
        if server_name:
            rel = url_path_join(user.base_url, url_escape_path(server_name), "/")
            return f"{HPC_PUBLIC_SCHEME}://{_job_host(jid)}{rel}"
        return f"{HPC_PUBLIC_SCHEME}://{_job_host(jid)}{user.base_url}"
    if public_url:
        return public_url
    if jid and getattr(spawner, "pending", None):
        if server_name:
            rel = url_path_join(user.base_url, url_escape_path(server_name), "/")
        else:
            rel = user.base_url
        return f"{HPC_PUBLIC_SCHEME}://{_job_host(jid)}{rel}"
    if server_name:
        return url_path_join(f"/user/{user.name}", url_escape_path(server_name), "/")
    return url_path_join(f"/user/{user.name}", "/")


def user_memory_overuse(user=None) -> dict:
    """ログインユーザーの各アプリについてメモリ超過状況をまとめて取得する。

    JupyterHub の template_vars は callable を ``value(user)`` として呼び出す。
    ホーム画面はアプリを複数並べるため、nvidia-smi と sstat をアプリごとに
    叩かないよう一度の取得で全ジョブ分を求める。

    Args:
        user: JupyterHubが渡すログインユーザー。

    Returns:
        server_nameをキー、超過状況を値とする辞書。取得できない場合は空。
    """
    spawners = getattr(user, "spawners", None) or {}
    targets = {}
    for server_name, spawner in spawners.items():
        # job_id は実行中に属性から消えることがあるため _spawner_job_id で回収する。
        # 一方でテンプレートからは同じ回収ができないので、返す辞書のキーには
        # 常に取得できる server_name を使う（キーが噛み合わず無表示になるのを防ぐ）。
        job_id = str(_spawner_job_id(spawner) or "")
        if not job_id:
            continue
        options = getattr(spawner, "user_options", None) or {}
        targets[str(server_name)] = (job_id, str(options.get("memory", "") or ""))
    if not targets:
        return {}
    try:
        from hpc_portal.infrastructure.slurm.slurm_client import (
            job_cpu_memory_bytes,
            job_gpu_memory_bytes,
            job_memory_usage,
            memory_overuse,
        )

        cpu = job_cpu_memory_bytes()
        gpu = job_gpu_memory_bytes()
    except Exception:
        return {}
    result = {}
    for server_name, (job_id, requested) in targets.items():
        usage = job_memory_usage(job_id, cpu, gpu)
        if usage["memory_used_bytes"] is None:
            continue
        overuse = memory_overuse(usage["memory_used_bytes"], requested)
        result[server_name] = {
            "memory_used_label": usage["memory_used_label"],
            "gpu_memory_label": (
                usage["gpu_memory_label"] if usage["gpu_memory_bytes"] else ""
            ),
            "memory_overuse_level": overuse["memory_overuse_level"],
            "memory_overuse_label": overuse["memory_overuse_label"],
        }
    return result


def spawner_memory_usage(job_id: str, requested: str) -> dict:
    """自分のアプリのメモリ実使用量と超過状況を取得する。

    Args:
        job_id: 対象のSlurm Job ID。
        requested: 要求メモリ（``16G`` などの表記）。

    Returns:
        実使用量と超過状況。取得できない場合は空の値を返す。
    """
    empty = {
        "memory_used_label": "",
        "gpu_memory_label": "",
        "memory_overuse_level": "",
        "memory_overuse_label": "",
    }
    if not job_id:
        return empty
    try:
        from hpc_portal.infrastructure.slurm.slurm_client import (
            job_cpu_memory_bytes,
            job_gpu_memory_bytes,
            job_memory_usage,
            memory_overuse,
        )

        usage = job_memory_usage(
            str(job_id), job_cpu_memory_bytes(), job_gpu_memory_bytes()
        )
        if usage["memory_used_bytes"] is None:
            return empty
        overuse = memory_overuse(usage["memory_used_bytes"], requested)
        return {
            "memory_used_label": usage["memory_used_label"],
            "gpu_memory_label": (
                usage["gpu_memory_label"] if usage["gpu_memory_bytes"] else ""
            ),
            "memory_overuse_level": overuse["memory_overuse_level"],
            "memory_overuse_label": overuse["memory_overuse_label"],
        }
    except Exception:
        return empty


def spawner_detail_context(spawner, server_name: str, user) -> dict:
    """アプリ詳細画面用の表示データ

    Args:
        spawner: 対象のSpawner。
        server_name: 対象のnamed server名。
        user: 対象のJupyterHubユーザー。

    Returns:
        アプリ詳細画面へ渡すコンテキスト。
    """
    uo = getattr(spawner, "user_options", None) or {}
    alloc = allocation_summary(uo)
    jid = _spawner_job_id(spawner) or getattr(spawner, "job_id", "") or ""
    pending = getattr(spawner, "pending", None)
    active = bool(getattr(spawner, "active", False))
    if pending:
        status = "pending"
        status_label = f"起動中 ({pending})"
    elif active:
        status = "running"
        status_label = "実行中"
    else:
        status = "unknown"
        status_label = "不明"
    port = getattr(spawner, "port", None) or ""
    app_choice = str(uo.get("app_choice", "ubuntu-cli"))
    return {
        "server_name": server_name,
        "server_name_path": server_name_to_path(server_name),
        "app_label": alloc["app_label"],
        "app_choice": app_choice,
        "status": status,
        "status_label": status_label,
        "pending": pending,
        "active": active,
        "job_id": jid,
        "job_url": spawner_job_url(spawner, server_name, user),
        "job_host": _job_host(jid) if jid else "",
        "allocation": alloc,
        "port": port,
        "public_url": getattr(spawner, "public_url", "") or "",
        "proxy_spec": getattr(spawner, "proxy_spec", "") or "",
        "openwebui_version": str(uo.get("openwebui_version", "")),
        "openwebui_target_version": HPC_OPENWEBUI_VERSION,
        # 旧構成のJupyterイメージもUbuntu 24.04固定だったため、保存値がない既存jobは現行設定値で補完する。
        "ubuntu_version": str(uo.get("ubuntu_version") or HPC_JUPYTER_UBUNTU_VERSION),
        "ubuntu_target_version": HPC_JUPYTER_UBUNTU_VERSION,
        # 統合メモリ構成ではGPUが確保した分も要求メモリの枠から出ていくが、
        # ConstrainRAMSpace=no のため超過しても停止しない。利用者自身が
        # 気付けるよう、自分のアプリの使用状況を渡す。
        **spawner_memory_usage(jid, alloc.get("memory", "")),
    }


# ホーム画面のアプリカードから自分のメモリ超過を見えるようにする
