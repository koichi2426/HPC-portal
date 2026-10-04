"""Slurmノードとホストの空きリソースを取得する。"""

import re
import subprocess

import psutil

from hpc_portal.infrastructure.config.settings import HPC_GPU_COUNT, SLURM_NODE_NAME


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


def slurm_allocated_gpus():
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


def slurm_free_resources():
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
        cpu_total = int(fields.get("CPUTot") or fields.get("CPUs") or cfg["cpu"] or 0)
        cpu_alloc = int(fields.get("CPUAlloc") or alloc["cpu"] or 0)
        # MemSpecLimit はOSとシステムデーモン用の予約で、ジョブへは割り当てられない。
        # RealMemory から引いた残りがSlurmの割り当て可能量になる。引き忘れると空きを
        # 過大報告し、投入前チェックが通した要求がPENDINGのまま詰まる。
        mem_total_mb = max(
            0,
            int(fields.get("RealMemory", "0") or 0)
            - int(fields.get("MemSpecLimit", "0") or 0),
        )
        mem_alloc_mb = alloc["mem_mb"]
        if not mem_alloc_mb and fields.get("AllocMem"):
            mem_alloc_mb = int(fields["AllocMem"])
        gpu_total = (
            _parse_slurm_gres_count(fields.get("Gres", "")) or cfg["gpu"] or gpu_default
        )
        gpu_alloc = alloc["gpu"]
        if not gpu_alloc:
            # AccountingStorageTRES に gres/gpu が無いと AllocTRES へ現れないため、
            # squeue の TRES_PER_NODE から実際の割当を補う。
            squeue_gpu = slurm_allocated_gpus()
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


def gpu_process_snapshot() -> tuple[list[dict], bool]:
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


# テンプレートからホーム画面のリソースメーターを描画する


class LinuxResourceInventory:
    metrics = psutil

    def slurm_free_resources(self):
        return slurm_free_resources()

    def gpu_process_snapshot(self):
        return gpu_process_snapshot()
