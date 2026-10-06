"""既存の暗号化レコードとAPIの業務モデルを相互に変換する。"""

from hpc_portal.domain.external_api.credentials import (
    ApiCredentials,
    ApiOwner,
    CredentialState,
)
from hpc_portal.domain.external_api.publication import (
    ApiPublication,
    ApiTarget,
    DesiredPublicationState,
    PublicationState,
)


class ApiAggregateRepository:
    def __init__(self, store):
        self.store = store

    def credentials_from_record(self, username, record):
        return ApiCredentials(
            ApiOwner(username, record["uid"], record["hub_user_id"]),
            bool(record.get("enabled")),
            CredentialState(record.get("state", "issuing")),
        )

    def credential_fields(self, credentials):
        return {
            "uid": credentials.owner.uid,
            "hub_user_id": credentials.owner.hub_user_id,
            "enabled": credentials.enabled,
            "state": credentials.state.value,
        }

    def load_credentials(self, username):
        record = self.store.get("credentials", username)
        return self.credentials_from_record(username, record) if record else None

    def save_credentials(self, credentials):
        name = credentials.owner.username
        record = self.store.get("credentials", name) or {}
        if record and not credentials.owner.matches(
            record["uid"], record["hub_user_id"]
        ):
            raise ValueError("所有者が変更された認証情報は更新できません")
        # domainが持たない秘密値は既存レコードへ残し、所有者と状態だけを更新する。
        record.update(self.credential_fields(credentials))
        self.store.put("credentials", name, record)

    def publication_from_record(self, record):
        return ApiPublication(
            ApiOwner(record["username"], record["uid"], record["hub_user_id"]),
            record["name"],
            ApiTarget(
                record["target"].get("uid", record["uid"]), record["target"]["port"]
            ),
            PublicationState(record["state"]),
            DesiredPublicationState(record["desired"]),
        )

    def publication_fields(self, publication):
        return {"state": publication.state.value, "desired": publication.desired.value}

    def load_publication(self, username, name):
        record = self.store.get("publications", username + "/" + name)
        return self.publication_from_record(record) if record else None

    def save_publication(self, publication):
        key = publication.owner.username + "/" + publication.name
        record = self.store.get("publications", key)
        if record is None:
            raise ValueError("APIの接続情報を保存してから状態を更新してください")
        if not publication.owner.matches(record["uid"], record["hub_user_id"]):
            raise ValueError("所有者が変更された公開設定は更新できません")
        record.update(self.publication_fields(publication))
        self.store.put("publications", key, record)
