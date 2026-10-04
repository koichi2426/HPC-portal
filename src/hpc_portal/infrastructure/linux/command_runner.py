"""秘密値を引数に載せずOSコマンドを実行する。"""

from hpc_portal.infrastructure.linux.user_account_gateway import run_cmd


class LinuxCommandRunner:
    def run(self, command, *, input_text=None, timeout=30):
        return run_cmd(command, input_text=input_text, timeout=timeout)
