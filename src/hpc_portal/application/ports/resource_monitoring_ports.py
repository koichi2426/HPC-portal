"""リソース監視に必要な読み取り操作。"""

from typing import Protocol


class ResourceInventory(Protocol):
    metrics: object

    def slurm_free_resources(self) -> dict | None: ...
    def gpu_process_snapshot(self) -> tuple[list[dict], bool]: ...
