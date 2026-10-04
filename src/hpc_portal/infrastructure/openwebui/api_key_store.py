"""Open WebUIキーを権限を制限したファイルへ保存する。"""

import os
import secrets

from hpc_portal.domain.accounts.account_policy import validate_username


class OpenWebuiKeyStore:
    def __init__(self, directory: str, protected_users=()):
        self.directory = directory
        self.protected_users = frozenset(protected_users)

    def path(self, username: str) -> str:
        """Open WebUI Keyの保存先を返す。

        Args:
            username: Linuxユーザー名。

        Returns:
            root専用Keyファイルの絶対パス。

        Raises:
            ValueError: ユーザー名がKey保存先として不正な場合。
        """
        if validate_username(username, self.protected_users):
            raise ValueError("Open WebUI key 用のユーザー名が不正です")
        return os.path.join(self.directory, f"{username}.key")

    def read(self, username: str) -> str:
        """保存済みOpen WebUI Keyを読む。

        Args:
            username: Linuxユーザー名。

        Returns:
            Key文字列。未保存または読込失敗時は空文字列。
        """
        try:
            with open(self.path(username), "r", encoding="utf-8") as key_file:
                return key_file.read().strip()
        except OSError:
            return ""

    def write(self, username: str, key: str) -> str | None:
        """Open WebUI Keyをroot専用ファイルへ原子的に保存する。

        Args:
            username: Linuxユーザー名。
            key: 保存する平文Virtual Key。

        Returns:
            正常ならNone、失敗時はエラーメッセージ。
        """
        if not key:
            return "Open WebUI 用 key が空です"
        try:
            os.makedirs(self.directory, mode=0o700, exist_ok=True)
            path = self.path(username)
            tmp_path = f"{path}.tmp-{os.getpid()}-{secrets.token_hex(6)}"
            fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, (key + "\n").encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp_path, path)
            os.chmod(path, 0o600)
        except OSError as exc:
            try:
                if "tmp_path" in locals():
                    os.unlink(tmp_path)
            except OSError:
                pass
            return f"Open WebUI 用 key の保存に失敗しました: {exc}"
        return None

    def remove(self, username: str) -> None:
        """保存済みOpen WebUI Keyファイルを削除する。

        Args:
            username: Linuxユーザー名。
        """
        try:
            os.unlink(self.path(username))
        except FileNotFoundError:
            pass
        except OSError:
            pass
