"""JupyterHubの専用トークンを発行・失効する。"""

from jupyterhub import orm, roles
from jupyterhub.utils import maybe_future


class HubTokenGateway:
    def __init__(self, app):
        """ユーザー取得と専用トークン発行に使うJupyterHubを保持する。

        Args:
            app: ユーザー・トークンのDBと設定を持つJupyterHubインスタンス。
        """
        self.app = app

    async def user(self, username):
        """Linuxユーザー名に対応するHubユーザーを取得し、未登録なら準備する。

        Args:
            username: 対象のLinuxユーザー名。

        Returns:
            既定ロールを付与したHubユーザー。
        """
        user = orm.User.find(self.app.db, username)
        if user is None:
            user = orm.User(name=username)
            self.app.db.add(user)
            self.app.db.commit()
        wrapped = self.app.users[user]
        roles.assign_default_roles(self.app.db, user)
        await maybe_future(self.app.authenticator.add_user(wrapped))
        return wrapped

    def issue(self, user):
        """本人の登録APIだけを呼び出せる専用Hubトークンを発行する。

        Args:
            user: 操作対象のJupyterHubユーザー。

        Returns:
            平文のhub_tokenと失効用のhub_token_id。
        """
        token = user.new_api_token(
            note="HPC external API",
            scopes=[f"custom:external-api:invoke!user={user.name}"],
        )
        record = orm.APIToken.find(self.app.db, token)
        return {"hub_token": token, "hub_token_id": record.id}

    def revoke(self, token_id):
        """指定したHubトークンを削除し、存在しない場合は何もしない。

        Args:
            token_id: 更新・失効させるトークンの識別子。
        """
        if token_id:
            record = self.app.db.query(orm.APIToken).filter_by(id=token_id).first()
            if record:
                self.app.db.delete(record)
                self.app.db.commit()

    def valid(self, record, user):
        """保存済みHubトークンのIDと所有者が現在も一致するか確認する。

        Args:
            record: 保存済みのHubトークンIDと所有者情報を含む認証情報レコード。
            user: 操作対象のJupyterHubユーザー。

        Returns:
            本人の有効なトークンとして確認できた場合はTrue。
        """
        token = orm.APIToken.find(self.app.db, record.get("hub_token", ""))
        return bool(
            token
            and token.id == record.get("hub_token_id")
            and token.user_id == user.id
        )

    def revoke_orphans(self, username, keep_id=None):
        """ユーザーに残る外部API用Hubトークンを、指定したID以外すべて削除する。

        Args:
            username: 対象のLinuxユーザー名。
            keep_id: 失効させずに残すトークンID。
        """
        user = orm.User.find(self.app.db, username)
        if user:
            for token in list(user.api_tokens):
                if token.note == "HPC external API" and token.id != keep_id:
                    self.revoke(token.id)
