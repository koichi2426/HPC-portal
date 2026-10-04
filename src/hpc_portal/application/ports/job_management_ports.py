"""ジョブ管理に必要な外部処理。"""

from typing import Protocol


class CommandRunner(Protocol):
    def run(self, command: list[str], *, input_text=None, timeout: int = 30): ...


class UserJobs(Protocol):
    def find_user(self, username: str): ...
