"""Slurmノードとホストの空きリソースを取得する。"""

import asyncio
import os
import re
import subprocess
import time

import psutil
from tornado import web

from .common import (
    BaseHandler,
    HPC_GPU_COUNT,
    HPC_PORTAL_CSS,
    HPC_PORTAL_JS_DIR,
    HPC_PORTAL_JS_FILES,
    SLURM_NODE_NAME,
    c,
)
from .schemas import HpcResourceSnapshot

def _hpc_resource_status(available_pct):
    """空き率を画面表示用の混雑度へ変換する。

    Args:
        available_pct: 0〜100の空き率。

    Returns:
        余裕あり、やや混雑、逼迫のいずれか。
    """
    if available_pct >= 50:
        return "余裕あり"
    if available_pct >= 25:
        return "やや混雑"
    return "逼迫"


def _parse_slurm_mem_to_mb(value: str) -> int:
    """Slurm TRES の mem=4G / mem=8192M などを MB に変換する

    Args:
        value: 変換または解析する値。

    Returns:
        MB単位のメモリ量。
    """
    s = str(value or "").strip().upper()
    if not s:
        return 0
    if s.endswith("G"):
        return int(float(s[:-1]) * 1024)
    if s.endswith("M"):
        return int(float(s[:-1]))
    return int(float(s))


def _parse_slurm_tres(tres: str) -> dict:
    """CfgTRES / AllocTRES を cpu・mem_mb・gpu に分解する

    Args:
        tres: 解析するSlurm TRES文字列。

    Returns:
        TRES名と値の辞書。
    """
    out = {"cpu": 0, "mem_mb": 0, "gpu": 0}
    if not tres:
        return out
    for part in str(tres).split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, _, val = part.partition("=")
        key = key.strip().lower()
        val = val.strip()
        if key == "cpu":
            out["cpu"] = int(float(val))
        elif key == "mem":
            out["mem_mb"] = _parse_slurm_mem_to_mb(val)
        elif key in ("gres/gpu", "gpu"):
            out["gpu"] = int(float(val))
    return out


def _slurm_field_map(line: str) -> dict:
    """scontrol -o の key=value 列を dict にする

    Args:
        line: 解析するSlurm出力行。

    Returns:
        フィールド名と値の辞書。
    """
    fields = {}
    for token in str(line or "").split():
        if "=" in token:
            key, _, val = token.partition("=")
            fields[key] = val
    return fields


def _parse_slurm_gres_count(gres: str) -> int:
    """ノード行の Gres=gpu:1 などから GPU 総数を得る

    Args:
        gres: 解析するSlurm GRES文字列。

    Returns:
        GPU数。
    """
    for part in str(gres or "").split(","):
        part = part.strip()
        if part.startswith("gpu:"):
            try:
                return int(part.split(":", 1)[1])
            except (TypeError, ValueError):
                return 0
    return 0


_SLURM_GPU_TRES_RE = re.compile(r"gres[/:]gpu(?::[A-Za-z0-9_.-]+)?:(\d+)")


def _parse_slurm_gpu_tres_per_node(raw: str) -> int:
    """squeue の TRES_PER_NODE 表記から GPU 要求数を取り出す

    Args:
        raw: squeue -o '%b' が返す gres/gpu:1 のような文字列。

    Returns:
        GPU要求数。GPUを要求していなければ0。
    """
    return sum(int(m.group(1)) for m in _SLURM_GPU_TRES_RE.finditer(str(raw or "")))


def _hpc_slurm_allocated_gpus():
    """実行中ジョブが確保しているGPU数を squeue から集計する

    AccountingStorageTRES に gres/gpu を含めていない構成では scontrol の
    AllocTRES へGPUが現れず、割当を検出できない。squeue の TRES_PER_NODE は
    その設定に依存しないため、そちらを集計する。

    Returns:
        確保済みGPU数。取得に失敗した場合はNone。
    """
    try:
        proc = subprocess.run(
            [
                "squeue",
                "--noheader",
                "--states=RUNNING",
                "--nodelist",
                SLURM_NODE_NAME,
                "-o",
                "%b",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if proc.returncode != 0:
            return None
        return sum(
            _parse_slurm_gpu_tres_per_node(line) for line in proc.stdout.splitlines()
        )
    except Exception:
        return None


def _hpc_slurm_free_resources():
    """Slurm が管理している未割り当て CPU / RAM / GPU（ジョブ停止で増える）

    Returns:
        Slurmが管理する空きリソース。
    """
    gpu_default = HPC_GPU_COUNT
    try:
        proc = subprocess.run(
            ["scontrol", "show", "node", SLURM_NODE_NAME, "-o"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        fields = _slurm_field_map(proc.stdout.strip())
        cfg = _parse_slurm_tres(fields.get("CfgTRES", ""))
        alloc = _parse_slurm_tres(fields.get("AllocTRES", ""))
        # Slurm 23.x の -o 出力は CPUTot / CPUAlloc（旧形式は CPUs）
        cpu_total = int(
            fields.get("CPUTot") or fields.get("CPUs") or cfg["cpu"] or 0
        )
        cpu_alloc = int(fields.get("CPUAlloc") or alloc["cpu"] or 0)
        mem_total_mb = int(fields.get("RealMemory", "0") or 0)
        mem_alloc_mb = alloc["mem_mb"]
        if not mem_alloc_mb and fields.get("AllocMem"):
            mem_alloc_mb = int(fields["AllocMem"])
        gpu_total = (
            _parse_slurm_gres_count(fields.get("Gres", ""))
            or cfg["gpu"]
            or gpu_default
        )
        gpu_alloc = alloc["gpu"]
        if not gpu_alloc:
            # AccountingStorageTRES に gres/gpu が無いと AllocTRES へ現れないため、
            # squeue の TRES_PER_NODE から実際の割当を補う。
            squeue_gpu = _hpc_slurm_allocated_gpus()
            if squeue_gpu is not None:
                gpu_alloc = squeue_gpu
        if cpu_total <= 0:
            return None
        cpu_free = max(0, cpu_total - cpu_alloc)
        mem_free_mb = max(0, mem_total_mb - mem_alloc_mb)
        gpu_free = max(0, gpu_total - gpu_alloc)
        return {
            "cpu_total": cpu_total,
            "cpu_available_count": float(cpu_free),
            "mem_total_mb": mem_total_mb,
            "mem_available_mb": mem_free_mb,
            "gpu_max": gpu_total,
            "gpu_available_count": gpu_free,
        }
    except Exception:
        return None


def _parse_nvidia_smi_memory_mb(value: str) -> int | None:
    """nvidia-smi の used_gpu_memory 列をMBへ変換する。

    Args:
        value: ``--format=csv,nounits`` が返す数値文字列。``[N/A]`` のこともある。

    Returns:
        MB単位の確保量。取得できなければNone。
    """
    raw = str(value or "").strip().rstrip("MiB").strip()
    if not raw or raw.startswith("["):
        return None
    try:
        return max(0, int(float(raw)))
    except (TypeError, ValueError):
        return None


def _hpc_gpu_process_snapshot() -> tuple[list[dict], bool]:
    """NVIDIA GPUを使用中の計算プロセスと、その確保量を取得する。

    GB10は統合メモリ構成で、CUDAが確保した分はプロセスのRSSにもcgroupにも
    現れない。nvidia-smi のプロセス単位の問い合わせだけが実際の確保量を返す。

    Returns:
        プロセス情報のリストと、取得に成功したかどうか。
    """
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-compute-apps=pid,process_name,used_memory",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
        if proc.returncode != 0:
            return [], False
        processes = []
        seen_pids = set()
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            # process_name にカンマが含まれても壊れないよう両端から切り出す。
            head, _, memory_raw = line.rpartition(",")
            pid_raw, _, name_raw = head.partition(",")
            if not head:
                continue
            try:
                pid = int(pid_raw.strip())
            except (TypeError, ValueError):
                continue
            if pid in seen_pids:
                continue
            seen_pids.add(pid)
            memory_mb = _parse_nvidia_smi_memory_mb(memory_raw)
            raw_name = name_raw.strip().rsplit("/", 1)[-1] or "不明"
            username = "不明"
            try:
                process = psutil.Process(pid)
                raw_name = process.name() or raw_name
                username = process.username() or username
            except (psutil.Error, OSError):
                pass
            name = "Ollama" if "ollama" in raw_name.lower() else raw_name
            processes.append(
                {
                    "pid": pid,
                    "name": name,
                    "username": username,
                    "memory_mb": memory_mb,
                }
            )
        processes.sort(key=lambda item: (item["username"], item["name"], item["pid"]))
        return processes, True
    except Exception:
        return [], False


def _hpc_resource_snapshot(disk_path="/home"):
    """CPU・統合メモリ・ストレージとGPUプロセスを取得する。

    Args:
        disk_path: ストレージ使用量を測定するパス。

    Returns:
        UI表示用に整形したリソース情報。
    """
    if not isinstance(disk_path, str):
        disk_path = "/home"
    slurm_free = _hpc_slurm_free_resources()
    mem = psutil.virtual_memory()
    if slurm_free:
        cpu_total = slurm_free["cpu_total"]
        cpu_available_count = slurm_free["cpu_available_count"]
        cpu_available = (
            max(0, min(100, cpu_available_count / cpu_total * 100)) if cpu_total else 0
        )
    else:
        cpu_total = psutil.cpu_count() or 0
        cpu = psutil.cpu_percent()
        cpu_available = max(0, min(100, 100 - cpu))
        cpu_available_count = cpu_total * cpu_available / 100
    # GB10はCPUとGPUが同じメモリを共有するため、OSの実空き容量も併せて示す。
    mem_available = max(0, min(100, mem.available / mem.total * 100))
    mem_available_gb = mem.available / (1024 ** 3)
    mem_total_gb = mem.total / (1024 ** 3)
    mem_used_gb = max(0, mem_total_gb - mem_available_gb)
    # ジョブが起動できるかを決めるのはOSの実空きではなく、Slurmが割り当て可能な残量。
    # 予約済みでもOS上は未使用のことがあり、実空きだけを見せると起動できない構成が
    # 「空きあり」に見えてしまう。
    if slurm_free and slurm_free["mem_total_mb"]:
        mem_slurm_total_gb = slurm_free["mem_total_mb"] / 1024
        mem_slurm_available_gb = slurm_free["mem_available_mb"] / 1024
    else:
        mem_slurm_total_gb = mem_total_gb
        mem_slurm_available_gb = mem_available_gb
    mem_slurm_used_gb = max(0, mem_slurm_total_gb - mem_slurm_available_gb)
    mem_slurm_available = (
        max(0, min(100, mem_slurm_available_gb / mem_slurm_total_gb * 100))
        if mem_slurm_total_gb
        else 0
    )
    try:
        disk = psutil.disk_usage(disk_path)
    except Exception:
        disk = psutil.disk_usage("/")
    disk_available = max(0, min(100, disk.free / disk.total * 100))
    disk_available_gb = disk.free / (1024 ** 3)
    disk_total_gb = disk.total / (1024 ** 3)
    gpu_max = (slurm_free["gpu_max"] if slurm_free else 0) or HPC_GPU_COUNT
    gpu_available_count = (
        slurm_free["gpu_available_count"] if slurm_free else gpu_max
    )
    gpu_available_count = max(0, min(gpu_max, int(gpu_available_count)))
    gpu_available = (
        max(0, min(100, gpu_available_count / gpu_max * 100)) if gpu_max else 0
    )
    gpu_processes, gpu_processes_available = _hpc_gpu_process_snapshot()
    # GB10は統合メモリのため、GPUの確保分は mem_total_gb の内数であり別枠ではない。
    # OSの実使用(mem_used_gb)にも現れないので、内訳として併記する。
    mem_gpu_used_gb = sum(
        (process["memory_mb"] or 0) for process in gpu_processes
    ) / 1024
    return {
        "cpu_available": cpu_available,
        "cpu_available_count": cpu_available_count,
        "cpu_total": cpu_total,
        "cpu_status": _hpc_resource_status(cpu_available),
        "mem_available": mem_available,
        "mem_available_gb": mem_available_gb,
        "mem_used_gb": mem_used_gb,
        "mem_total_gb": mem_total_gb,
        "mem_gpu_used_gb": mem_gpu_used_gb,
        "mem_status": _hpc_resource_status(mem_available),
        "mem_slurm_available": mem_slurm_available,
        "mem_slurm_available_gb": mem_slurm_available_gb,
        "mem_slurm_used_gb": mem_slurm_used_gb,
        "mem_slurm_total_gb": mem_slurm_total_gb,
        "mem_slurm_status": _hpc_resource_status(mem_slurm_available),
        "disk_available": disk_available,
        "disk_available_gb": disk_available_gb,
        "disk_total_gb": disk_total_gb,
        "disk_status": _hpc_resource_status(disk_available),
        "gpu_max": gpu_max,
        "gpu_available": gpu_available,
        "gpu_available_count": gpu_available_count,
        "gpu_status": _hpc_resource_status(gpu_available),
        "gpu_processes": gpu_processes,
        "gpu_process_count": len(gpu_processes),
        "gpu_processes_available": gpu_processes_available,
    }


# テンプレートからホーム画面のリソースメーターを描画する
c.JupyterHub.template_vars["hpc_resource_snapshot"] = _hpc_resource_snapshot


class HpcResourceStatusHandler(BaseHandler):
    """ホーム/起動フォームのリソースメーターを定期更新するための JSON API"""

    @web.authenticated
    async def get(self):
        """現在の空きリソースをキャッシュ無効のJSONで返す。"""
        self.set_header("Cache-Control", "no-store, no-cache, must-revalidate")
        payload = await asyncio.to_thread(_hpc_resource_snapshot)
        payload["updated_at"] = time.time()
        self.write(HpcResourceSnapshot.model_validate(payload).model_dump())


class HpcPortalJsHandler(BaseHandler):
    """HPCポータルの責務別JavaScriptを配信する。"""

    async def get(self, filename: str):
        """許可済みJavaScriptファイルを返す。

        Args:
            filename: URLで指定されたJavaScriptファイル名。
        """
        self.set_header("Content-Type", "application/javascript; charset=UTF-8")
        self.set_header("Cache-Control", "public, max-age=300")
        self.set_header("X-Content-Type-Options", "nosniff")
        if filename not in HPC_PORTAL_JS_FILES:
            raise web.HTTPError(404)
        try:
            path = os.path.join(HPC_PORTAL_JS_DIR, filename)
            with open(path, encoding="utf-8") as f:
                self.write(f.read())
        except OSError:
            self.set_status(404)
            self.write("/* hpc portal JavaScript not found */")


class HpcPortalCssHandler(BaseHandler):
    """HPC ポータル共通スタイルシート（/hub/static が使えない環境向け）"""

    async def get(self):
        """HPCポータル共通CSSを返す。"""
        self.set_header("Content-Type", "text/css; charset=UTF-8")
        self.set_header("Cache-Control", "public, max-age=300")
        try:
            with open(HPC_PORTAL_CSS, encoding="utf-8") as f:
                self.write(f.read())
        except OSError:
            self.set_status(404)
            self.write("/* hpc-portal.css not found */")
