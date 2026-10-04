"""同一モデルのダウンロード監視を一度だけ開始する。"""

import asyncio


class ModelPullScheduler:
    def __init__(self, watch_pull, tasks):
        self.watch_pull = watch_pull
        self.tasks = tasks

    def start(self, model):
        task = self.tasks.get(model)
        if task is None or task.done():
            self.tasks[model] = asyncio.create_task(self.watch_pull(model))
