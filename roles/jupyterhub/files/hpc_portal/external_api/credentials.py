"""Idempotent per-user issuance and independent rotations with durable phases."""
import asyncio
import pwd
import time
from jupyterhub import orm, roles
from jupyterhub.utils import maybe_future


class HubTokens:
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
        token = user.new_api_token(note="HPC external API", scopes=[
            f"custom:external-api:invoke!user={user.name}"])
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
        return bool(token and token.id == record.get("hub_token_id") and token.user_id == user.id)

    def revoke_orphans(self, username, keep_id=None):
        user = orm.User.find(self.app.db, username)
        if user:
            for token in list(user.api_tokens):
                if token.note == "HPC external API" and token.id != keep_id:
                    self.revoke(token.id)


class Credentials:
    def __init__(self, store, cloudflare, hub, config):
        self.store, self.cf, self.hub, self.config = store, cloudflare, hub, config
        self.locks = {}
        self.common_lock = asyncio.Lock()

    def lock(self, username):
        return self.locks.setdefault(username, asyncio.Lock())

    def identity(self, user):
        return {"uid": pwd.getpwnam(user.name).pw_uid, "hub_user_id": user.id}

    def record(self, user):
        record = self.store.get("credentials", user.name)
        if not record or any(record.get(k) != v for k, v in self.identity(user).items()):
            raise ValueError("接続情報は発行待ちです")
        return record

    async def ensure(self, user):
        async with self.lock(user.name):
            identity = self.identity(user)
            record = self.store.get("credentials", user.name)
            if record and any(record.get(k) != v for k, v in identity.items()):
                await self._disable(user.name, record)
                self.store.delete("credentials", user.name)
                record = None
            record = record or {**identity, "enabled": True, "state": "issuing"}
            if not record["enabled"]:
                return record
            if record.get("state") == "ready" and self.hub.valid(record, user):
                return record
            self.store.put("credentials", user.name, record)
            if not record.get("cf_token_id"):
                token = await self.cf.issue(f"HPC {self.config.public_host} {user.name} {identity['uid']} {identity['hub_user_id']}")
                record.update(cf_token_id=token["id"], client_id=token["client_id"], client_secret=token["client_secret"])
                self.store.put("credentials", user.name, record)
            if record.get("state") == "rotating_cloudflare":
                token = await self.cf.rotate(record["cf_token_id"])
                record.update(client_secret=token["client_secret"])
                record["state"] = "issuing"
                self.store.put("credentials", user.name, record)
            if (not self.hub.valid(record, user) or
                    record.get("hub_token_id") in record.get("revoke_pending", [])):
                # Revoke orphaned tokens from an interrupted issuance before creating another.
                for token in list(getattr(user, "api_tokens", [])):
                    if token.note == "HPC external API" and token.id != record.get("hub_token_id"):
                        self.hub.revoke(token.id)
                record.update(self.hub.issue(user))
                self.store.put("credentials", user.name, record)
            for token_id in record.get("revoke_pending", []):
                self.hub.revoke(token_id)
            record.pop("revoke_pending", None)
            record.update(state="ready", updated_at=time.time())
            self.store.put("credentials", user.name, record)
            return record

    async def rotate(self, user, kind):
        if kind not in {"cloudflare", "jupyterhub"}:
            raise ValueError("再発行対象が不正です")
        async with self.lock(user.name):
            record = self.record(user)
            if not record["enabled"] or record["state"] != "ready":
                raise ValueError("接続情報が準備できていません。管理者へ確認してください")
            record["state"] = "rotating_" + kind
            self.store.put("credentials", user.name, record)
            if kind == "cloudflare":
                token = await self.cf.rotate(record["cf_token_id"])
                record["client_secret"] = token["client_secret"]
            else:
                old_id = record["hub_token_id"]
                record["revoke_pending"] = [old_id]
                self.store.put("credentials", user.name, record)
                record.update(self.hub.issue(user))
                self.store.put("credentials", user.name, record)
                self.hub.revoke(old_id)
                record.pop("revoke_pending", None)
            record.update(state="ready", updated_at=time.time())
            self.store.put("credentials", user.name, record)
            return record

    async def _disable(self, username, record):
        record.update(enabled=False, state="revoking")
        self.store.put("credentials", username, record)
        self.hub.revoke(record.get("hub_token_id"))
        if hasattr(self.hub, "revoke_orphans"):
            self.hub.revoke_orphans(username)
        for token_id in record.get("revoke_pending", []):
            self.hub.revoke(token_id)
        if record.get("cf_token_id"):
            await self.cf.remove(record["cf_token_id"])
        record = {"uid": record["uid"], "hub_user_id": record["hub_user_id"], "enabled": False, "state": "disabled"}
        self.store.put("credentials", username, record)

    async def disable(self, username):
        async with self.lock(username):
            record = self.store.get("credentials", username)
            if record:
                await self._disable(username, record)

    async def enable(self, user):
        async with self.lock(user.name):
            record = self.store.get("credentials", user.name)
            if record and record.get("state") != "disabled":
                raise ValueError("失効処理が完了していません")
            self.store.delete("credentials", user.name)
        return await self.ensure(user)
