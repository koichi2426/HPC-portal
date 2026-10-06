"""OSコマンド実行を共通化し、パスワード等を標準入力で渡せるようにする。"""

from hpc_portal.infrastructure.linux.user_account_gateway import run_cmd


class LinuxCommandRunner:
    def run(self, command, *, input_text=None, timeout=30):
        """OSコマンドを実行し、秘密値などを標準入力で渡す。

        Args:
            command: 実行するOSコマンドと引数の配列。
            input_text: コマンドの標準入力へ渡す文字列。
            timeout: 処理完了を待つ上限時間。単位は秒。

        Returns:
            終了コード・標準出力・標準エラーを持つ実行結果。
        """
        return run_cmd(command, input_text=input_text, timeout=timeout)
