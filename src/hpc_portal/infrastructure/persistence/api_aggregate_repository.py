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
        """APIのdomainモデルを読み書きするレコード保存先を保持する。

        Args:
            store: 認証情報・公開設定・ポート保護のレコード保存先。
        """
        self.store = store

    def credentials_from_record(self, username, record):
        """保存レコードから秘密値を除き、認証情報のdomainモデルへ変換する。

        Args:
            username: 対象のLinuxユーザー名。
            record: 所有者・状態・秘密値を含む認証情報の保存レコード。

        Returns:
            所有者と有効状態・発行状態を持つ認証情報。
        """
        return ApiCredentials(
            ApiOwner(username, record["uid"], record["hub_user_id"]),
            bool(record.get("enabled")),
            CredentialState(record.get("state", "issuing")),
        )

    def credential_fields(self, credentials):
        """認証情報のdomainモデルから、保存する所有者と状態を取り出す。

        Args:
            credentials: 所有者・有効状態・発行状態を持つ認証情報のdomainモデル。

        Returns:
            UID・HubユーザーID・有効状態・発行状態の辞書。
        """
        return {
            "uid": credentials.owner.uid,
            "hub_user_id": credentials.owner.hub_user_id,
            "enabled": credentials.enabled,
            "state": credentials.state.value,
        }

    def load_credentials(self, username):
        """ユーザーの保存レコードを認証情報のdomainモデルへ変換する。

        Args:
            username: 対象のLinuxユーザー名。

        Returns:
            認証情報のdomainモデル。未登録ならNone。
        """
        record = self.store.get("credentials", username)
        return self.credentials_from_record(username, record) if record else None

    def save_credentials(self, credentials):
        """所有者を照合し、保存済みの秘密値を残して認証状態を更新する。

        Args:
            credentials: 所有者・有効状態・発行状態を持つ認証情報のdomainモデル。

        Raises:
            ValueError: 保存済みレコードと所有者が一致しない場合。
        """
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
        """保存済みの接続先と状態を、API公開設定のdomainモデルへ変換する。

        Args:
            record: 所有者・接続先・希望状態を含むAPI公開の保存レコード。

        Returns:
            所有者・接続先・現在の状態・希望状態を持つ公開設定。
        """
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
        """API公開設定から、保存する現在の状態と希望状態を取り出す。

        Args:
            publication: 保存するAPI公開設定のdomainモデル。

        Returns:
            stateとdesiredの辞書。
        """
        return {"state": publication.state.value, "desired": publication.desired.value}

    def load_publication(self, username, name):
        """ユーザーとAPI名を指定し、公開設定のdomainモデルを読み込む。

        Args:
            username: 対象のLinuxユーザー名。
            name: ユーザーが登録したAPIの識別名。

        Returns:
            API公開設定のdomainモデル。未登録ならNone。
        """
        record = self.store.get("publications", username + "/" + name)
        return self.publication_from_record(record) if record else None

    def save_publication(self, publication):
        """保存済み接続先の所有者を照合し、現在の状態と希望状態を更新する。

        Args:
            publication: 保存するAPI公開設定のdomainモデル。

        Raises:
            ValueError: 接続先が未保存、または保存済みの所有者と一致しない場合。
        """
        key = publication.owner.username + "/" + publication.name
        record = self.store.get("publications", key)
        if record is None:
            raise ValueError("APIの接続情報を保存してから状態を更新してください")
        if not publication.owner.matches(record["uid"], record["hub_user_id"]):
            raise ValueError("所有者が変更された公開設定は更新できません")
        record.update(self.publication_fields(publication))
        self.store.put("publications", key, record)
