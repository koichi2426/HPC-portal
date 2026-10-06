"""APIの登録・認証情報の読み取りと、公開先の状態確認。"""

import asyncio

from hpc_portal.domain.external_api.credentials import ApiOwner


class ApiRecordQueries:
    def __init__(self, store, accounts, credential_repository, publication_repository):
        """APIレコードの所有者照合に使う依存とユーザー単位のロックを準備する。

        Args:
            store: 認証情報・公開設定・ポート保護のレコード保存先。
            accounts: Linuxユーザーの照合・作成・変更を行う接続先。
            credential_repository: 認証情報の所有者と状態を読み書きする保存先。
            publication_repository: API公開設定の所有者と状態を読み書きする保存先。
        """
        self.store = store
        self.accounts = accounts
        self.credential_repository = credential_repository
        self.publication_repository = publication_repository
        self.credential_locks = {}
        self.publication_locks = {}

    def credential_lock(self, username):
        """同一ユーザーの認証情報更新を直列化するロックを取得する。

        Args:
            username: 対象のLinuxユーザー名。

        Returns:
            ユーザー単位の非同期ロック。
        """
        return self.credential_locks.setdefault(username, asyncio.Lock())

    def credential_identity(self, user):
        """現在のLinux UIDとHubユーザーIDを取得する。

        Args:
            user: 操作対象のJupyterHubユーザー。

        Returns:
            認証情報と照合するuid・hub_user_idの辞書。
        """
        return {"uid": self.accounts.getpwnam(user.name).pw_uid, "hub_user_id": user.id}

    def credential_record(self, user):
        """現在の所有者と一致する認証情報レコードを取得する。

        Args:
            user: 操作対象のJupyterHubユーザー。

        Returns:
            本人の認証情報レコード。

        Raises:
            ValueError: 現在の所有者に対応する認証情報が未登録の場合。
        """
        record = self.store.get("credentials", user.name)
        credentials = self.credential_repository.load_credentials(user.name)
        identity = self.credential_identity(user)
        if not credentials or not credentials.owner.matches(
            identity["uid"], identity["hub_user_id"]
        ):
            raise ValueError("接続情報は発行待ちです")

        return record

    def publication_lock(self, username):
        """同一ユーザーのAPI公開操作を直列化するロックを取得する。

        Args:
            username: 対象のLinuxユーザー名。

        Returns:
            ユーザー単位の非同期ロック。
        """
        return self.publication_locks.setdefault(username, asyncio.Lock())

    def publication_key(self, username, name):
        """ユーザー名とAPI名から保存キーを組み立てる。

        Args:
            username: 対象のLinuxユーザー名。
            name: ユーザーが登録したAPIの識別名。

        Returns:
            ユーザー名/API名の形式のキー。
        """
        return username + "/" + name

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
        record = self.store.get("publications", self.publication_key(user.name, name))
        publication = self.publication_repository.load_publication(user.name, name)
        if publication is None:
            raise ValueError("API の登録が見つかりません")

        # 保存キーに同じ名前があっても、UIDとHubのIDが変わった登録は返さない。
        publication.require_owner(
            ApiOwner(user.name, self.accounts.getpwnam(user.name).pw_uid, user.id)
        )

        return record

    def list_publications(self, user):
        """現在の所有者と一致するAPI公開登録を列挙する。

        Args:
            user: 操作対象のJupyterHubユーザー。

        Returns:
            本人のAPI公開レコード一覧。
        """
        records = []
        for key in self.store.names("publications"):
            if key.startswith(user.name + "/"):
                try:
                    records.append(self.get_publication(user, key.split("/", 1)[1]))
                except ValueError:
                    continue
        return records
