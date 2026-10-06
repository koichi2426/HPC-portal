"""ユーザー管理に必要な外部処理の契約。"""

from typing import Protocol


class AccountDirectory(Protocol):
    def getpwnam(self, username: str):
        """Linuxユーザー名からアカウント情報を取得する。

        Args:
            username: 対象のLinuxユーザー名。

        Returns:
            UID・ホームなどを持つLinuxアカウント情報。

        Raises:
            KeyError: Linuxユーザーが存在しない場合。
        """
        ...


class UserAccountGateway(AccountDirectory, Protocol):
    def linux_users_snapshot(self) -> list[dict]:
        """ポータル管理対象のLinuxユーザー一覧を取得する。

        Returns:
            ユーザー名、表示名、UID、ホーム、シェル、保護状態、sudo状態を含む辞書の一覧。
        """
        ...

    def home_storage_usage(self, home: str) -> tuple[int | None, str | None]:
        """ホームディレクトリが実際に使用しているストレージ量を取得する。

        Args:
            home: 集計対象のホームディレクトリ。

        Returns:
            ``(使用バイト数, エラー)``。集計は同一ファイルシステム内に限定する。
        """
        ...

    def create_linux_user(
        self, username: str, password: str, grant_sudo: bool, display_name: str
    ) -> str | None:
        """Linuxユーザーを作成して初期パスワードを設定する。

        Args:
            username: 作成するLinuxユーザー名。
            password: 設定する初期パスワード。
            grant_sudo: sudoグループへ追加するか。
            display_name: 管理画面へ表示する任意の名前。

        Returns:
            正常ならNone、失敗時はエラーメッセージ。
        """
        ...

    def delete_linux_user(self, username: str, actor: str) -> str | None:
        """ユーザーのジョブとプロセスを停止してLinuxユーザーを削除する。

        Args:
            username: 削除対象のLinuxユーザー名。
            actor: 操作中の管理者ユーザー名。

        Returns:
            正常ならNone、失敗時はエラーメッセージ。
        """
        ...

    def set_linux_sudo(self, username: str, enabled: bool) -> str | None:
        """Linuxユーザーのsudoグループ所属を変更する。

        Args:
            username: 変更対象のLinuxユーザー名。
            enabled: Trueならsudoを付与し、Falseなら解除する。

        Returns:
            正常ならNone、失敗時はエラーメッセージ。
        """
        ...

    def set_linux_display_name(self, username: str, display_name: str) -> str | None:
        """Linux GECOS欄の表示名を設定または削除する。

        Args:
            username: 対象のLinuxユーザー名。
            display_name: 設定する表示名。

        Returns:
            正常ならNone、失敗時はエラーメッセージ。
        """
        ...

    def set_linux_password(self, username: str, password: str) -> str | None:
        """Linuxユーザーのパスワードを再設定する。

        Args:
            username: 対象のLinuxユーザー名。
            password: 新しい平文パスワード。

        Returns:
            正常ならNone、失敗時はエラーメッセージ。
        """
        ...

    def verify_linux_password(
        self, username: str, password: str, *, service: str
    ) -> str | None:
        """PAMでログイン中ユーザーの現在のパスワードを確認する。

        Args:
            username: 対象のLinuxユーザー名。
            password: 確認する平文パスワード。
            service: PAM認証で使用するサービス名。

        Returns:
            認証成功ならTrueとNone、失敗時はFalseとエラーの組。
        """
        ...
