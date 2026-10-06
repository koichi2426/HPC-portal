"""リソース監視に必要な読み取り操作。"""

from typing import Protocol


class ResourceInventory(Protocol):
    metrics: object

    def slurm_free_resources(self) -> dict | None:
        """Slurm が管理している未割り当て CPU / RAM / GPU（ジョブ停止で増える）

        Returns:
            Slurmが管理する空きリソース。
        """
        ...

    def gpu_process_snapshot(self) -> tuple[list[dict], bool]:
        """NVIDIA GPUを使用中の計算プロセスと、その確保量を取得する。

        GB10は統合メモリ構成で、CUDAが確保した分はプロセスのRSSにもcgroupにも
        現れない。nvidia-smi のプロセス単位の問い合わせだけが実際の確保量を返す。

        Returns:
            プロセス情報のリストと、取得に成功したかどうか。
        """
        ...
