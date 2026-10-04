"""APIの登録・認証情報の読み取りと、公開先の状態確認。"""

import asyncio

from hpc_portal.domain.external_api.credentials import ApiOwner


class ApiRecordQueries:
    def __init__(self, store, accounts, credential_repository, publication_repository):
        self.store = store
        self.accounts = accounts
        self.credential_repository = credential_repository
        self.publication_repository = publication_repository
        self.credential_locks = {}
        self.publication_locks = {}

    def credential_lock(self, username):
        return self.credential_locks.setdefault(username, asyncio.Lock())

    def credential_identity(self, user):
        return {"uid": self.accounts.getpwnam(user.name).pw_uid, "hub_user_id": user.id}

    def credential_record(self, user):
        record = self.store.get("credentials", user.name)
        credentials = self.credential_repository.load_credentials(user.name)
        identity = self.credential_identity(user)
        if not credentials or not credentials.owner.matches(
            identity["uid"], identity["hub_user_id"]
        ):
            raise ValueError("接続情報は発行待ちです")
        return record

    def publication_lock(self, username):
        return self.publication_locks.setdefault(username, asyncio.Lock())

    def publication_key(self, username, name):
        return username + "/" + name

    def get_publication(self, user, name):
        record = self.store.get("publications", self.publication_key(user.name, name))
        publication = self.publication_repository.load_publication(user.name, name)
        if publication is None:
            raise ValueError("API の登録が見つかりません")
        publication.require_owner(
            ApiOwner(user.name, self.accounts.getpwnam(user.name).pw_uid, user.id)
        )
        return record

    def list_publications(self, user):
        records = []
        for key in self.store.names("publications"):
            if key.startswith(user.name + "/"):
                try:
                    records.append(self.get_publication(user, key.split("/", 1)[1]))
                except ValueError:
                    continue
        return records
