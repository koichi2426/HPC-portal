"""管理者向け全ユーザー起動中アプリAPIを提供する。"""

import asyncio
import glob
import re
import subprocess
import threading
import time

from tornado import web

from ..common import BaseHandler
from ..schemas import HpcAdminAppsResponse
from ..users import (
    _hpc_is_portal_admin,
    _hpc_linux_users_snapshot,
    _hpc_run_cmd,
)
from .utils import _hpc_format_storage_bytes

_HPC_ADMIN_APPS_CACHE_SECONDS = 5.0
_HPC_ADMIN_APPS_CACHE_LOCK = threading.Lock()
_HPC_ADMIN_APPS_CACHE: dict = {
    "expires_at": 0.0,
    "apps": [],
    "error": "",
}
# nvidia-smi の結果は利用者に依らないため全体で共有する。ホーム画面も5秒間隔で
# 参照するため、閲覧者の人数分だけプロセスを起動しないよう短いキャッシュを置く。
_HPC_GPU_MEMORY_CACHE_SECONDS = 3.0
_HPC_GPU_MEMORY_CACHE_LOCK = threading.Lock()
_HPC_GPU_MEMORY_CACHE: dict = {
    "expires_at": 0.0,
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
        value: ``40G`` / ``8192M`` のようなSlurmのメモリ表記。

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

def _hpc_job_memory_usage(
    job_id: str, cpu_memory: dict[str, int], gpu_memory: dict[str, int]
) -> dict:
    """1ジョブのメモリ使用量を表示用にまとめる。

    CPU側(cgroup)とGPU側(nvidia-smi)は会計が重複しないため、合計が実使用量に
    なる。どちらも瞬間値なので、同時には使っていない量を足すことはない。

    Args:
        job_id: 対象のSlurm Job ID。
        cpu_memory: ``_hpc_job_cpu_memory_bytes`` の結果。
        gpu_memory: ``_hpc_job_gpu_memory_bytes`` の結果。

    Returns:
        表示用のバイト数とラベル。取得できない場合はNoneと「取得不可」。
    """
    cpu_bytes = cpu_memory.get(str(job_id))
    gpu_bytes = gpu_memory.get(str(job_id))
    if cpu_bytes is None and gpu_bytes is None:
        return {
            "cpu_memory_bytes": None,
            "cpu_memory_label": "取得不可",
            "gpu_memory_bytes": None,
            "gpu_memory_label": "—",
            "memory_used_bytes": None,
            "memory_used_label": "取得不可",
        }
    total = (cpu_bytes or 0) + (gpu_bytes or 0)
    return {
        "cpu_memory_bytes": cpu_bytes,
        "cpu_memory_label": (
            _hpc_format_storage_bytes(cpu_bytes) if cpu_bytes is not None else "—"
        ),
        "gpu_memory_bytes": gpu_bytes,
        "gpu_memory_label": (
            _hpc_format_storage_bytes(gpu_bytes) if gpu_bytes else "—"
        ),
        "memory_used_bytes": total,
        "memory_used_label": _hpc_format_storage_bytes(total),
    }


_HPC_MEMORY_OVERUSE_WARN_RATIO = 1.2


def _hpc_memory_overuse(used_bytes: int | None, requested: str) -> dict:
    """要求メモリに対する実使用量の超過状況を判定する。

    ConstrainRAMSpace=no のため --mem は物理的な上限ではなく、統合メモリ構成では
    GPUが確保した分もこの枠から出ていく。超過していても誰も止めないため、
    利用者と管理者が気付けるよう状態だけを返す。

    Args:
        used_bytes: CPU側とGPU側を合算した実使用量。取得できなければNone。
        requested: ``squeue`` が返す要求メモリ（``40G`` など）。

    Returns:
        使用率と警告レベルを含む辞書。判定できない場合は使用率をNoneにする。
    """
    limit_bytes = _hpc_slurm_memory_bytes(requested)
    if not used_bytes or not limit_bytes:
        return {
            "memory_limit_bytes": limit_bytes,
            "memory_usage_ratio": None,
            "memory_overuse_level": "",
            "memory_overuse_label": "",
        }
    ratio = used_bytes / limit_bytes
    if ratio > _HPC_MEMORY_OVERUSE_WARN_RATIO:
        level = "warning"
    elif ratio > 1.0:
        level = "caution"
    else:
        level = ""
    return {
        "memory_limit_bytes": limit_bytes,
        "memory_usage_ratio": ratio,
        "memory_overuse_level": level,
        # 割合だけでは「あとどれだけ要求を増やせばよいか」が分からないため、
        # 実使用量と超過量を実数で示す。
        "memory_overuse_label": (
            f"要求 {_hpc_format_storage_bytes(limit_bytes)} に対して "
            f"{_hpc_format_storage_bytes(used_bytes)} を使用しています"
            f"（{_hpc_format_storage_bytes(used_bytes - limit_bytes)} 超過 / "
            f"{ratio * 100:.0f}%）"
            if level
            else ""
        ),
    }


# 実機で確認したSlurmのcgroup階層。job_N 直下が1ジョブ分の合計で、配下の
# step_batch/... は同じ量の内訳なので拾わない。
_HPC_JOB_CGROUP_GLOBS = (
    "/sys/fs/cgroup/system.slice/*slurmstepd.scope/job_*/memory.current",
    "/sys/fs/cgroup/*/*slurmstepd*/job_*/memory.current",
)


def _hpc_job_cpu_memory_bytes() -> dict[str, int]:
    """ジョブごとのCPU側メモリ使用量を cgroup から取得する。

    sstat の MaxRSS はピーク値で、nvidia-smi が返す現在値と足すと同時には
    使っていない量まで合算してしまう。memory.current は瞬間値であり、
    ConstrainRAMSpace が制限する対象そのもののため、GPU側と足す指標として
    正しい。JobAcctGatherFrequency=30 の制約も受けない。

    Returns:
        Job IDをキー、CPU側メモリのバイト数を値とする辞書。
    """
    usage: dict[str, int] = {}
    for pattern in _HPC_JOB_CGROUP_GLOBS:
        for path in glob.glob(pattern):
            match = re.search(r"/job_(\d+)/memory\.current$", path)
            if not match:
                continue
            try:
                with open(path, encoding="utf-8") as handle:
                    usage[match.group(1)] = max(0, int(handle.read().strip()))
            except (OSError, ValueError):
                continue
        if usage:
            break
    return usage


def _hpc_job_id_of_pid(pid: int) -> str:
    """PIDが属するSlurmジョブのIDを cgroup から逆引きする。

    Args:
        pid: 対象プロセスのPID。

    Returns:
        Slurm Job ID。ジョブに属していなければ空文字列。
    """
    try:
        with open(f"/proc/{pid}/cgroup", encoding="utf-8") as handle:
            content = handle.read()
    except OSError:
        return ""
    match = re.search(r"/job_(\d+)\b", content)
    return match.group(1) if match else ""


def _hpc_job_gpu_memory_bytes() -> dict[str, int]:
    """ジョブごとのGPU確保量を nvidia-smi から集計する。

    GB10は統合メモリ構成で、CUDAが確保した分はプロセスのRSSにもcgroupにも
    現れない。cgroup の値だけでは実使用量を1/10ほどに見誤るため、
    プロセス単位の確保量をジョブへ足し戻す。

    Returns:
        Job IDをキー、GPU確保量のバイト数を値とする辞書。
    """
    with _HPC_GPU_MEMORY_CACHE_LOCK:
        if time.monotonic() < _HPC_GPU_MEMORY_CACHE["expires_at"]:
            return dict(_HPC_GPU_MEMORY_CACHE["usage"])
    usage = _hpc_job_gpu_memory_bytes_uncached()
    with _HPC_GPU_MEMORY_CACHE_LOCK:
        _HPC_GPU_MEMORY_CACHE.update(
            {
                "expires_at": time.monotonic() + _HPC_GPU_MEMORY_CACHE_SECONDS,
                "usage": dict(usage),
            }
        )
    return usage


def _hpc_job_gpu_memory_bytes_uncached() -> dict[str, int]:
    """ジョブごとのGPU確保量を nvidia-smi から集計する（キャッシュなし）。

    Returns:
        Job IDをキー、GPU確保量のバイト数を値とする辞書。
    """
    try:
        result = _hpc_run_cmd(
            [
                "nvidia-smi",
                "--query-compute-apps=pid,used_memory",
                "--format=csv,noheader,nounits",
            ],
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0:
        return {}
    usage: dict[str, int] = {}
    for line in result.stdout.splitlines():
        pid_raw, separator, memory_raw = line.partition(",")
        if not separator:
            continue
        try:
            pid = int(pid_raw.strip())
            megabytes = int(float(memory_raw.strip()))
        except (TypeError, ValueError):
            continue
        job_id = _hpc_job_id_of_pid(pid)
        if not job_id:
            continue
        usage[job_id] = usage.get(job_id, 0) + max(0, megabytes) * 1024**2
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
    cpu_memory = _hpc_job_cpu_memory_bytes()
    gpu_memory = _hpc_job_gpu_memory_bytes()
    for row in rows:
        row.update(_hpc_job_memory_usage(row["job_id"], cpu_memory, gpu_memory))
        row.update(_hpc_memory_overuse(row["memory_used_bytes"], row["memory"]))
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
        response = HpcAdminAppsResponse.model_validate(
            {"apps": apps, "error": error, "updated_at": time.time()}
        )
        self.write(response.model_dump())
