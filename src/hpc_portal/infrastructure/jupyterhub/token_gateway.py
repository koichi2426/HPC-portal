"""JupyterHubの専用トークンを発行・失効する。"""

from jupyterhub import orm, roles
from jupyterhub.utils import maybe_future


class HubTokenGateway:
    def __init__(self, app):
        self.app = app

    async def user(self, username):
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
        token = user.new_api_token(
            note="HPC external API",
            scopes=[f"custom:external-api:invoke!user={user.name}"],
        )
        record = orm.APIToken.find(self.app.db, token)
        return {"hub_token": token, "hub_token_id": record.id}

    def revoke(self, token_id):
        if token_id:
            record = self.app.db.query(orm.APIToken).filter_by(id=token_id).first()
            if record:
                self.app.db.delete(record)
                self.app.db.commit()

    def valid(self, record, user):
        token = orm.APIToken.find(self.app.db, record.get("hub_token", ""))
        return bool(
            token
            and token.id == record.get("hub_token_id")
            and token.user_id == user.id
        )

    def revoke_orphans(self, username, keep_id=None):
        user = orm.User.find(self.app.db, username)
        if user:
            for token in list(user.api_tokens):
                if token.note == "HPC external API" and token.id != keep_id:
                    self.revoke(token.id)
