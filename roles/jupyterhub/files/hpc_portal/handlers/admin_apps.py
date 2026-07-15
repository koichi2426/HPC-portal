"""管理者向け全ユーザー起動中アプリAPIを提供する。"""

import asyncio
import re
import subprocess
import threading
import time

from tornado import web

from ..common import BaseHandler
from ..users import (
    _hpc_is_portal_admin,
    _hpc_linux_users_snapshot,
    _hpc_run_cmd,
)
from .utils import _hpc_format_storage_bytes

_HPC_ADMIN_APPS_CACHE_SECONDS = 5.0
_HPC_ADMIN_APPS_RSS_CACHE_SECONDS = 30.0
_HPC_ADMIN_APPS_CACHE_LOCK = threading.Lock()
_HPC_ADMIN_APPS_CACHE: dict = {
    "expires_at": 0.0,
    "apps": [],
    "error": "",
}
_HPC_ADMIN_APPS_RSS_CACHE: dict = {
    "expires_at": 0.0,
    "job_ids": (),
    "usage": {},
}
_HPC_PORTAL_SLURM_APPS = {
    "jhub-app": "JupyterLab",
    "jhub-openwebui": "Open WebUI",
    "shared-ollama": "Ollama",
}

def _hpc_slurm_memory_bytes(value: str) -> int | None:
    """SlurmのK/M/G/T表記をバイトへ変換する。

    Args:
        value: ``sstat``が返すMaxRSSなどの値。

    Returns:
        バイト数。空値または不正値の場合はNone。
    """
    raw = str(value or "").strip().upper()
    if not raw or raw in {"N/A", "UNKNOWN", "-"}:
        return None
    multipliers = {
        "K": 1024,
        "M": 1024**2,
        "G": 1024**3,
        "T": 1024**4,
    }
    suffix = raw[-1]
    multiplier = multipliers.get(suffix, 1)
    number = raw[:-1] if suffix in multipliers else raw
    try:
        return max(0, int(float(number) * multiplier))
    except (TypeError, ValueError):
        return None

def _hpc_slurm_max_rss(job_ids: list[str]) -> dict[str, int]:
    """実行中Slurmジョブの最大RSSをまとめて取得する。

    Args:
        job_ids: 確認するSlurm Job IDの一覧。

    Returns:
        Job IDをキー、最大RSSのバイト数を値とする辞書。
    """
    if not job_ids:
        return {}
    cache_key = tuple(sorted(set(job_ids)))
    now = time.monotonic()
    if (
        _HPC_ADMIN_APPS_RSS_CACHE["job_ids"] == cache_key
        and now < _HPC_ADMIN_APPS_RSS_CACHE["expires_at"]
    ):
        return dict(_HPC_ADMIN_APPS_RSS_CACHE["usage"])
    try:
        result = _hpc_run_cmd(
            [
                "sstat",
                "--jobs=" + ",".join(f"{job_id}.batch" for job_id in job_ids),
                "--noheader",
                "--parsable2",
                "--format=JobID,MaxRSS",
            ],
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        usage = {}
    else:
        usage = {}
        if result.returncode == 0:
            requested = set(job_ids)
            for line in result.stdout.splitlines():
                step_id, separator, rss_value = line.partition("|")
                if not separator:
                    continue
                job_id = step_id.strip().split(".", 1)[0]
                if job_id not in requested:
                    continue
                rss_bytes = _hpc_slurm_memory_bytes(rss_value.rstrip("|"))
                if rss_bytes is not None:
                    usage[job_id] = max(usage.get(job_id, 0), rss_bytes)
    _HPC_ADMIN_APPS_RSS_CACHE.update(
        {
            "expires_at": time.monotonic() + _HPC_ADMIN_APPS_RSS_CACHE_SECONDS,
            "job_ids": cache_key,
            "usage": dict(usage),
        }
    )
    return usage

def _hpc_admin_apps_snapshot() -> tuple[list[dict], str]:
    """ポータルから起動したSlurmアプリの割当と利用状況を取得する。

    Returns:
        アプリ情報の一覧と、取得失敗時のメッセージ。
    """
    with _HPC_ADMIN_APPS_CACHE_LOCK:
        now = time.monotonic()
        if now < _HPC_ADMIN_APPS_CACHE["expires_at"]:
            return (
                [dict(app) for app in _HPC_ADMIN_APPS_CACHE["apps"]],
                str(_HPC_ADMIN_APPS_CACHE["error"]),
            )
        apps, error = _hpc_admin_apps_snapshot_uncached()
        _HPC_ADMIN_APPS_CACHE.update(
            {
                "expires_at": time.monotonic() + _HPC_ADMIN_APPS_CACHE_SECONDS,
                "apps": [dict(app) for app in apps],
                "error": error,
            }
        )
        return apps, error

def _hpc_admin_apps_snapshot_uncached() -> tuple[list[dict], str]:
    """Slurmへ問い合わせてポータル由来の起動中アプリを取得する。

    Returns:
        アプリ情報の一覧と、取得失敗時のメッセージ。
    """
    try:
        result = _hpc_run_cmd(
            [
                "squeue",
                "--noheader",
                "--format=%i|%u|%j|%T|%C|%m|%b|%M|%S",
            ],
            timeout=3,
        )
    except subprocess.TimeoutExpired:
        return [], "Slurmからの応答がタイムアウトしました"
    except OSError as exc:
        return [], str(exc)[:300]
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "squeue failed").strip()
        return [], message[:300]
    display_names = {
        row["username"]: row.get("display_name", "")
        for row in _hpc_linux_users_snapshot()
    }
    rows = []
    for line in result.stdout.splitlines():
        fields = [field.strip() for field in line.split("|", 8)]
        if len(fields) != 9:
            continue
        job_id, username, job_name, state, cpus, memory, gres, elapsed, started_at = fields
        app_label = _HPC_PORTAL_SLURM_APPS.get(job_name)
        if not app_label:
            continue
        gpu_match = re.search(r"(?:^|[/,:])gpu(?::[^,:]+)?:(\d+)", gres, re.I)
        gpu_count = int(gpu_match.group(1)) if gpu_match else 0
        rows.append(
            {
                "job_id": job_id,
                "username": username,
                "display_name": "共有" if job_name == "shared-ollama" else display_names.get(username, ""),
                "app": app_label,
                "state": state,
                "state_label": {
                    "RUNNING": "実行中",
                    "PENDING": "実行待ち",
                    "COMPLETING": "停止処理中",
                    "CONFIGURING": "起動処理中",
                }.get(state, state or "不明"),
                "cpus": cpus or "—",
                "memory": memory or "—",
                "gpus": gpu_count,
                "elapsed": elapsed or "—",
                "started_at": "—" if started_at in {"", "N/A", "Unknown"} else started_at,
            }
        )
    max_rss = _hpc_slurm_max_rss([row["job_id"] for row in rows if row["state"] == "RUNNING"])
    for row in rows:
        rss_bytes = max_rss.get(row["job_id"])
        row["max_rss_bytes"] = rss_bytes
        if rss_bytes is not None:
            row["max_rss_label"] = _hpc_format_storage_bytes(rss_bytes)
        elif row["state"] == "RUNNING":
            row["max_rss_label"] = "取得不可"
        else:
            row["max_rss_label"] = "計測待ち"
    rows.sort(key=lambda row: (row["username"], row["app"], row["job_id"]))
    return rows, ""

class HpcAdminAppsApiHandler(BaseHandler):
    """管理者へポータル由来の起動中Slurmアプリ一覧を返す。"""

    @web.authenticated
    async def get(self):
        """アプリの割当と最大RSSをJSONで返す。

        Raises:
            web.HTTPError: ポータル管理者ではない場合。
        """
        if not _hpc_is_portal_admin(self.current_user):
            raise web.HTTPError(403, "管理者のみアクセスできます")
        apps, error = await asyncio.to_thread(_hpc_admin_apps_snapshot)
        self.set_header("Cache-Control", "no-store")
        self.write({"apps": apps, "error": error, "updated_at": time.time()})
