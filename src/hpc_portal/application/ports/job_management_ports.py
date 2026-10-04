"""ジョブ管理に必要な外部処理。"""

from typing import Protocol


class CommandRunner(Protocol):
    def run(self, command: list[str], *, input_text=None, timeout: int = 30): ...


class UserJobs(Protocol):
    def active_servers(self, username: str, application: str) -> list[str]: ...
    async def stop_server(self, username: str, server_name: str) -> None: ...
