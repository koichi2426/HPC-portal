"""Durable publication registry, independent of application launch and shutdown."""
import asyncio
import pwd
import time
from urllib.parse import quote

from .inventory import public_candidate
from .transport import request


class Publications:
    def __init__(self, store, config, credentials, inventory, guard, access):
        self.store, self.config, self.credentials = store, config, credentials
        self.inventory, self.guard, self.access = inventory, guard, access
        self.locks = {}

    def lock(self, username):
        return self.locks.setdefault(username, asyncio.Lock())

    def key(self, username, name):
        return username + "/" + name

    def url(self, record):
        return f"https://{self.config.public_host}/hub/user-api/{quote(record['username'], safe='')}/{record['name']}/"

    def get(self, user, name):
        record = self.store.get("publications", self.key(user.name, name))
        if not record or record["uid"] != pwd.getpwnam(user.name).pw_uid or record["hub_user_id"] != user.id:
            raise ValueError("API の登録が見つかりません")
        return record

    def list(self, user):
        records = []
        for key in self.store.names("publications"):
            if key.startswith(user.name + "/"):
                try:
                    records.append(self.get(user, key.split("/", 1)[1]))
                except ValueError:
                    continue
        return records

    def public(self, record):
        home = pwd.getpwnam(record["username"]).pw_dir
        target = public_candidate(record["target"], home)
        return {k: record.get(k) for k in ("name", "display_name", "state", "health_path", "checked_at")} | {
            "url": self.url(record), "workdir": target["workdir"], "port": target["port"],
            "started_at": target["started_at"]}

    async def probe(self, record):
        await self.guard.check(record["target"])
        async with request(self.inventory, record["target"], "GET" if record["health_path"] else "HEAD",
                           record["health_path"] or "/", timeout=3) as response:
            if record["health_path"] and not 200 <= response.status < 300:
                raise ValueError("動作確認パスが正常な応答を返していません")

    async def register(self, user, settings):
        async with self.lock(user.name):
            key = self.key(user.name, settings.name)
            old = self.store.get("publications", key)
            if old and (old["uid"] != pwd.getpwnam(user.name).pw_uid or old["hub_user_id"] != user.id):
                raise ValueError("以前のアカウントの登録が残っています。管理者に確認してください")
            if not old and len(self.list(user)) >= self.config.max_apps:
                raise ValueError("API の登録数上限に達しています")
            credential = self.credentials.record(user)
            if not credential.get("enabled") or credential.get("state") != "ready":
                raise ValueError("外部 API の接続情報が準備できていません")
            target = await asyncio.to_thread(self.inventory.choose, credential["uid"], settings.candidate, settings.port)
            record = {**settings.model_dump(exclude={"candidate", "port"}), "username": user.name,
                "uid": credential["uid"], "hub_user_id": user.id, "target": target,
                "display_name": settings.display_name or target["display_name"],
                "state": "checking", "desired": "published", "remote_clean": False, "created_at": old["created_at"] if old else time.time()}
            if old:
                record.update({k: old[k] for k in ("cf_app_id", "cf_aud") if k in old})
            # Save local deny before changing a target or invoking remote APIs.
            self.store.put("publications", key, record)
            return await self._publish(record, credential)

    async def _publish(self, record, credential):
        key = self.key(record["username"], record["name"])
        record["state"] = "checking"
        record["remote_clean"] = False
        self.store.put("publications", key, record)
        try:
            await self.guard.protect(record["target"])
            await self.probe(record)
            record["state"] = "configuring"
            self.store.put("publications", key, record)
            base = f"{self.config.public_host}/hub/user-api/{quote(record['username'], safe='')}/{record['name']}"
            app = await self.credentials.cf.app(
                f"HPC API / {self.config.public_host} / {record['username']} / {record['name']}",
                [base, base + "/*"], [credential["cf_token_id"]], record.get("cf_app_id"))
            record.update(cf_app_id=app["id"], cf_aud=app["aud"], cf_checked_at=time.time())
            # Process may have ended during the Cloudflare update.
            await asyncio.to_thread(self.inventory.validate, record["target"])
            record["state"] = "published"
        except Exception:
            record["state"] = "error"
            raise
        finally:
            record["checked_at"] = time.time()
            self.store.put("publications", key, record)
        return record

    async def operate(self, user, name, action):
        async with self.lock(user.name):
            record = self.get(user, name)
            key = self.key(user.name, name)
            if action == "publish":
                credential = self.credentials.record(user)
                if not credential.get("enabled") or credential.get("state") != "ready":
                    raise ValueError("外部 API 接続が利用停止中です")
                record["desired"] = "published"
                return await self._publish(record, credential)
            if action not in {"unpublish", "delete"}:
                raise ValueError("操作が不正です")
            record.update(desired="deleted" if action == "delete" else "unpublished", state="unpublished")
            self.store.put("publications", key, record)
            await self._remove_remote(record)
            if action == "delete":
                self.store.delete("publications", key)
            return record

    async def _remove_remote(self, record):
        if record.get("remote_clean") and not record.get("cf_app_id"):
            return
        # Deterministic-name lookup also handles lost create responses.
        await self.credentials.cf.remove_app(
            f"HPC API / {self.config.public_host} / {record['username']} / {record['name']}", record.get("cf_app_id"))
        record.pop("cf_app_id", None)
        record.pop("cf_aud", None)
        record["remote_clean"] = True
        self.store.put("publications", self.key(record["username"], record["name"]), record)

    async def refresh(self, record):
        async with self.lock(record["username"]):
            key = self.key(record["username"], record["name"])
            record = self.store.get("publications", key)
            if not record:
                return
            if record["desired"] != "published":
                await self._remove_remote(record)
                if record["desired"] == "deleted":
                    self.store.delete("publications", key)
                return
            credential = self.store.get("credentials", record["username"])
            if not credential or not credential.get("enabled") or credential.get("state") != "ready":
                record["state"] = "disabled"
            elif any(record[k] != credential[k] for k in ("uid", "hub_user_id")):
                record.update(state="unpublished", desired="deleted")
            else:
                try:
                    await asyncio.to_thread(self.inventory.validate, record["target"])
                except ValueError:
                    record["state"] = "disconnected"
                else:
                    if record["state"] in {"checking", "configuring", "error", "disabled"} or time.time() - record.get("cf_checked_at", 0) >= 300:
                        return await self._publish(record, credential)
                    try:
                        await self.probe(record)
                        record["state"] = "published"
                    except Exception:
                        record["state"] = "disconnected"
            record["checked_at"] = time.time()
            self.store.put("publications", key, record)

    async def unpublish_all(self, username):
        async with self.lock(username):
            records = [self.store.get("publications", k) for k in self.store.names("publications") if k.startswith(username + "/")]
            for record in records:
                record.update(desired="unpublished", state="unpublished")
                self.store.put("publications", self.key(username, record["name"]), record)
            for record in records:
                await self._remove_remote(record)
