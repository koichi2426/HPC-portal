"""外部APIの所有者照合済みレコード取得と、ユーザー単位の排他制御の契約。"""

from typing import Protocol


class ApiQueries(Protocol):
    def credential_lock(self, username):
        """同一ユーザーの認証情報更新を直列化するロックを取得する。

        Args:
            username: 対象のLinuxユーザー名。

        Returns:
            ユーザー単位の非同期ロック。
        """
        ...

    def credential_identity(self, user):
        """現在のLinux UIDとHubユーザーIDを取得する。

        Args:
            user: 操作対象のJupyterHubユーザー。

        Returns:
            認証情報と照合するuid・hub_user_idの辞書。
        """
        ...

    def credential_record(self, user):
        """現在の所有者と一致する認証情報レコードを取得する。

        Args:
            user: 操作対象のJupyterHubユーザー。

        Returns:
            本人の認証情報レコード。

        Raises:
            ValueError: 現在の所有者に対応する認証情報が未登録の場合。
        """
        ...

    def publication_lock(self, username):
        """同一ユーザーのAPI公開操作を直列化するロックを取得する。

        Args:
            username: 対象のLinuxユーザー名。

        Returns:
            ユーザー単位の非同期ロック。
        """
        ...

    def publication_key(self, username, name):
        """ユーザー名とAPI名から保存キーを組み立てる。

        Args:
            username: 対象のLinuxユーザー名。
            name: ユーザーが登録したAPIの識別名。

        Returns:
            ユーザー名/API名の形式のキー。
        """
        ...

    def get_publication(self, user, name):
        """指定したAPI登録の所有者を照合して取得する。

        Args:
            user: 操作対象のJupyterHubユーザー。
            name: ユーザーが登録したAPIの識別名。

        Returns:
            本人のAPI公開レコード。

        Raises:
            ValueError: 本人のAPI登録を取得できない場合。
        """
        ...

    def list_publications(self, user):
        """現在の所有者と一致するAPI公開登録を列挙する。

        Args:
            user: 操作対象のJupyterHubユーザー。

        Returns:
            本人のAPI公開レコード一覧。
        """
        ...
