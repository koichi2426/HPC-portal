"""Slurmノードとホストの空きリソースを取得する。"""

from .common import (
    BaseHandler,
    HPC_APP_STATUS_JS,
    HPC_GPU_COUNT,
    HPC_PORTAL_CSS,
    HPC_RESOURCE_METER_JS,
    SLURM_NODE_NAME,
    c,
    psutil,
    re,
    subprocess,
    time,
    web,
)

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
    """Slurm TRES の mem=4G / mem=8192M などを MB に変換する"""
    s = str(value or "").strip().upper()
    if not s:
        return 0
    if s.endswith("G"):
        return int(float(s[:-1]) * 1024)
    if s.endswith("M"):
        return int(float(s[:-1]))
    return int(float(s))


def _parse_slurm_tres(tres: str) -> dict:
    """CfgTRES / AllocTRES を cpu・mem_mb・gpu に分解する"""
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
    """scontrol -o の key=value 列を dict にする"""
    fields = {}
    for token in str(line or "").split():
        if "=" in token:
            key, _, val = token.partition("=")
            fields[key] = val
    return fields


def _parse_slurm_gres_count(gres: str) -> int:
    """ノード行の Gres=gpu:1 などから GPU 総数を得る"""
    for part in str(gres or "").split(","):
        part = part.strip()
        if part.startswith("gpu:"):
            try:
                return int(part.split(":", 1)[1])
            except (TypeError, ValueError):
                return 0
    return 0


def _hpc_slurm_free_resources():
    """Slurm が管理している未割り当て CPU / RAM / GPU（ジョブ停止で増える）"""
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


def _hpc_nvidia_vram_snapshot():
    """GPU VRAM の実使用量（Slurm 解放後もプロセス残りで減らないことがある）"""
    gpu_vram_available_gb = 0.0
    gpu_vram_total_gb = 0.0
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.total,memory.used",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
        gpu_total_mb = 0
        gpu_used_mb = 0
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 2:
                continue
            total_mb = int(float(parts[0].replace(" MiB", "").replace("MiB", "").strip()))
            used_mb = int(float(parts[1].replace(" MiB", "").replace("MiB", "").strip()))
            gpu_total_mb += total_mb
            gpu_used_mb += used_mb
        if gpu_total_mb > 0:
            gpu_vram_available_gb = (gpu_total_mb - gpu_used_mb) / 1024
            gpu_vram_total_gb = gpu_total_mb / 1024
    except Exception:
        pass
    return gpu_vram_available_gb, gpu_vram_total_gb


def _hpc_resource_snapshot(disk_path="/home"):
    """CPU、メモリ、ストレージ、GPUの空き状況を取得する。

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
        mem_total_gb = slurm_free["mem_total_mb"] / 1024
        mem_available_gb = slurm_free["mem_available_mb"] / 1024
        mem_available = (
            max(0, min(100, mem_available_gb / mem_total_gb * 100)) if mem_total_gb else 0
        )
    else:
        cpu_total = psutil.cpu_count() or 0
        cpu = psutil.cpu_percent()
        cpu_available = max(0, min(100, 100 - cpu))
        cpu_available_count = cpu_total * cpu_available / 100
        mem_available = max(0, min(100, mem.available / mem.total * 100))
        mem_available_gb = mem.available / (1024 ** 3)
        mem_total_gb = mem.total / (1024 ** 3)
    try:
        disk = psutil.disk_usage(disk_path)
    except Exception:
        disk = psutil.disk_usage("/")
    disk_available = max(0, min(100, disk.free / disk.total * 100))
    disk_available_gb = disk.free / (1024 ** 3)
    disk_total_gb = disk.total / (1024 ** 3)
    gpu_max = HPC_GPU_COUNT
    gpu_available = 0
    gpu_available_count = 0
    if slurm_free:
        gpu_max = slurm_free["gpu_max"] or gpu_max
        gpu_available_count = slurm_free["gpu_available_count"]
        gpu_available = (
            max(0, min(100, gpu_available_count / gpu_max * 100)) if gpu_max else 0
        )
    elif gpu_max > 0:
        try:
            gpu_query = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.total,memory.used",
                    "--format=csv,noheader,nounits",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            gpu_lines = [line.strip() for line in gpu_query.stdout.splitlines() if line.strip()]
            gpu_total_mb = 0
            gpu_used_mb = 0
            gpu_free_count = 0
            for line in gpu_lines:
                parts = [p.strip() for p in line.split(",")[:2]]
                total_mb = int(float(parts[0].replace(" MiB", "").replace("MiB", "").strip()))
                used_mb = int(float(parts[1].replace(" MiB", "").replace("MiB", "").strip()))
                gpu_total_mb += total_mb
                gpu_used_mb += used_mb
                if total_mb > 0 and used_mb / total_mb < 0.10:
                    gpu_free_count += 1
            if gpu_total_mb > 0:
                gpu_available = max(0, min(100, (gpu_total_mb - gpu_used_mb) / gpu_total_mb * 100))
                gpu_available_count = gpu_free_count
        except Exception:
            pass
    gpu_vram_available_gb, gpu_vram_total_gb = _hpc_nvidia_vram_snapshot()
    return {
        "cpu_available": cpu_available,
        "cpu_available_count": cpu_available_count,
        "cpu_total": cpu_total,
        "cpu_status": _hpc_resource_status(cpu_available),
        "mem_available": mem_available,
        "mem_available_gb": mem_available_gb,
        "mem_total_gb": mem_total_gb,
        "mem_status": _hpc_resource_status(mem_available),
        "disk_available": disk_available,
        "disk_available_gb": disk_available_gb,
        "disk_total_gb": disk_total_gb,
        "disk_status": _hpc_resource_status(disk_available),
        "gpu_available": gpu_available,
        "gpu_available_count": gpu_available_count,
        "gpu_max": gpu_max,
        "gpu_vram_available_gb": gpu_vram_available_gb,
        "gpu_vram_total_gb": gpu_vram_total_gb,
        "gpu_status": _hpc_resource_status(gpu_available) if gpu_max > 0 else "未検出",
    }


# テンプレートからホーム画面のリソースメーターを描画する
c.JupyterHub.template_vars["hpc_resource_snapshot"] = _hpc_resource_snapshot


class HpcResourceStatusHandler(BaseHandler):
    """ホーム/起動フォームのリソースメーターを定期更新するための JSON API"""

    @web.authenticated
    async def get(self):
        """現在の空きリソースをキャッシュ無効のJSONで返す。"""
        self.set_header("Cache-Control", "no-store, no-cache, must-revalidate")
        payload = _hpc_resource_snapshot()
        payload["updated_at"] = time.time()
        self.write(payload)


class HpcResourceMeterJsHandler(BaseHandler):
    """リソースメーター自動更新スクリプト（/hub/static が使えない環境向け）"""

    async def get(self):
        """リソースメーター用JavaScriptを返す。"""
        self.set_header("Content-Type", "application/javascript; charset=UTF-8")
        self.set_header("Cache-Control", "public, max-age=300")
        try:
            with open(HPC_RESOURCE_METER_JS, encoding="utf-8") as f:
                self.write(f.read())
        except OSError:
            self.set_status(404)
            self.write("/* hpc-resource-meter.js not found */")


class HpcAppStatusJsHandler(BaseHandler):
    """アプリ起動状態の自動更新スクリプトを配信する。"""

    async def get(self):
        """アプリ状態ポーリング用JavaScriptを返す。"""
        self.set_header("Content-Type", "application/javascript; charset=UTF-8")
        self.set_header("Cache-Control", "public, max-age=300")
        try:
            with open(HPC_APP_STATUS_JS, encoding="utf-8") as f:
                self.write(f.read())
        except OSError:
            self.set_status(404)
            self.write("/* hpc-app-status.js not found */")


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
