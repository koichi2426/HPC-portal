"""同一モデルのダウンロード監視を一度だけ開始する。"""

import asyncio


class ModelPullScheduler:
    def __init__(self, watch_pull, tasks):
        """モデル取得の完了監視と、実行中タスクの共有先を保持する。

        Args:
            watch_pull: ダウンロード完了を監視してモデルを登録する非同期関数。
            tasks: モデル名と実行中の監視タスクを対応させる共有辞書。
        """
        self.watch_pull = watch_pull
        self.tasks = tasks

    def start(self, model):
        """同一モデルの監視が動いていない場合だけ、完了監視タスクを開始する。

        Args:
            model: 操作するOllamaモデルの名前。
        """
        task = self.tasks.get(model)
        if task is None or task.done():
            self.tasks[model] = asyncio.create_task(self.watch_pull(model))
