"""ジョブ管理に必要な外部処理。"""

from typing import Protocol


class CommandRunner(Protocol):
    def run(self, command: list[str], *, input_text=None, timeout: int = 30):
        """OSコマンドを実行し、秘密値などを標準入力で渡す。

        Args:
            command: 実行するOSコマンドと引数の配列。
            input_text: コマンドの標準入力へ渡す文字列。
            timeout: 処理完了を待つ上限時間。単位は秒。

        Returns:
            終了コード・標準出力・標準エラーを持つ実行結果。
        """
        ...


class UserJobs(Protocol):
    def active_servers(self, username: str, application: str) -> list[str]:
        """指定ユーザー・アプリに該当する稼働中または起動中のserver名を取得する。

        Args:
            username: 対象のLinuxユーザー名。
            application: 対象アプリの識別名。

        Returns:
            該当するnamed server名の一覧。ユーザー未登録なら空の一覧。
        """
        ...

    async def stop_server(self, username: str, server_name: str) -> None:
        """指定ユーザーのnamed serverを停止する。未登録ユーザーは処理しない。

        Args:
            username: 対象のLinuxユーザー名。
            server_name: 対象のnamed server名。空文字列はデフォルトserverを表す。
        """
        ...
