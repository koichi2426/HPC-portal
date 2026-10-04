"""OSとSlurmの情報を合わせて空きリソースを確認する。"""

from hpc_portal.application.ports.resource_monitoring_ports import ResourceInventory
from hpc_portal.domain.resources.snapshot import resource_status


class ResourceQueryUseCase:
    def __init__(self, inventory: ResourceInventory, gpu_count: int):
        self.inventory = inventory
        self.gpu_count = gpu_count

    def execute(self, disk_path="/home"):
        """CPU・統合メモリ・ストレージとGPUプロセスを取得する。

        Args:
            disk_path: ストレージ使用量を測定するパス。

        Returns:
            UI表示用に整形したリソース情報。
        """
        if not isinstance(disk_path, str):
            disk_path = "/home"
        slurm_free = self.inventory.slurm_free_resources()
        mem = self.inventory.metrics.virtual_memory()
        if slurm_free:
            cpu_total = slurm_free["cpu_total"]
            cpu_available_count = slurm_free["cpu_available_count"]
            cpu_available = (
                max(0, min(100, cpu_available_count / cpu_total * 100))
                if cpu_total
                else 0
            )
        else:
            cpu_total = self.inventory.metrics.cpu_count() or 0
            cpu = self.inventory.metrics.cpu_percent()
            cpu_available = max(0, min(100, 100 - cpu))
            cpu_available_count = cpu_total * cpu_available / 100
        # GB10はCPUとGPUが同じメモリを共有するため、OSの実空き容量も併せて示す。
        mem_available = max(0, min(100, mem.available / mem.total * 100))
        mem_available_gb = mem.available / (1024**3)
        mem_total_gb = mem.total / (1024**3)
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
            disk = self.inventory.metrics.disk_usage(disk_path)
        except Exception:
            disk = self.inventory.metrics.disk_usage("/")
        disk_available = max(0, min(100, disk.free / disk.total * 100))
        disk_available_gb = disk.free / (1024**3)
        disk_total_gb = disk.total / (1024**3)
        gpu_max = (slurm_free["gpu_max"] if slurm_free else 0) or self.gpu_count
        gpu_available_count = (
            slurm_free["gpu_available_count"] if slurm_free else gpu_max
        )
        gpu_available_count = max(0, min(gpu_max, int(gpu_available_count)))
        gpu_available = (
            max(0, min(100, gpu_available_count / gpu_max * 100)) if gpu_max else 0
        )
        gpu_processes, gpu_processes_available = self.inventory.gpu_process_snapshot()
        # GB10は統合メモリのため、GPUの確保分は mem_total_gb の内数であり別枠ではない。
        # OSの実使用(mem_used_gb)にも現れないので、内訳として併記する。
        mem_gpu_used_gb = (
            sum((process["memory_mb"] or 0) for process in gpu_processes) / 1024
        )
        return {
            "cpu_available": cpu_available,
            "cpu_available_count": cpu_available_count,
            "cpu_total": cpu_total,
            "cpu_status": resource_status(cpu_available),
            "mem_available": mem_available,
            "mem_available_gb": mem_available_gb,
            "mem_used_gb": mem_used_gb,
            "mem_total_gb": mem_total_gb,
            "mem_gpu_used_gb": mem_gpu_used_gb,
            "mem_status": resource_status(mem_available),
            "mem_slurm_available": mem_slurm_available,
            "mem_slurm_available_gb": mem_slurm_available_gb,
            "mem_slurm_used_gb": mem_slurm_used_gb,
            "mem_slurm_total_gb": mem_slurm_total_gb,
            "mem_slurm_status": resource_status(mem_slurm_available),
            "disk_available": disk_available,
            "disk_available_gb": disk_available_gb,
            "disk_total_gb": disk_total_gb,
            "disk_status": resource_status(disk_available),
            "gpu_max": gpu_max,
            "gpu_available": gpu_available,
            "gpu_available_count": gpu_available_count,
            "gpu_status": resource_status(gpu_available),
            "gpu_processes": gpu_processes,
            "gpu_process_count": len(gpu_processes),
            "gpu_processes_available": gpu_processes_available,
        }
