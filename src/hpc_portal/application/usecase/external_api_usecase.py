"""個人別トークンの発行・再発行と、既存HTTP APIの公開手順。"""

import asyncio
import logging
import secrets
import time
from urllib.parse import quote

from hpc_portal.application.ports.external_api_ports import (
    AccessVerifier,
    CloudflareAccess,
    HttpRelay,
    HubTokens,
    ListenerInventory,
    PortGuard,
    RecordStore,
)
from hpc_portal.application.ports.user_management_ports import AccountDirectory
from hpc_portal.domain.errors import UseCaseError
from hpc_portal.domain.external_api_models import public_candidate

log = logging.getLogger("jupyterhub.external-api")


class ExternalApiUseCase:
    def __init__(
        self,
        store: RecordStore,
        cloudflare: CloudflareAccess,
        hub: HubTokens,
        config,
        inventory: ListenerInventory,
        guard: PortGuard,
        access: AccessVerifier,
        accounts: AccountDirectory,
        relay: HttpRelay,
        users_snapshot,
    ):
        self.store = store
        self.cf = cloudflare
        self.hub = hub
        self.config = config
        self.inventory = inventory
        self.guard = guard
        self.access = access
        self.accounts = accounts
        self.relay = relay
        self.users_snapshot = users_snapshot
        self.credential_locks = {}
        self.publication_locks = {}
        self.sync_lock = asyncio.Lock()

    def credential_lock(self, username):
        return self.credential_locks.setdefault(username, asyncio.Lock())

    def credential_identity(self, user):
        return {"uid": self.accounts.getpwnam(user.name).pw_uid, "hub_user_id": user.id}

    def credential_record(self, user):
        record = self.store.get("credentials", user.name)
        if not record or any(
            record.get(k) != v for k, v in self.credential_identity(user).items()
        ):
            raise ValueError("接続情報は発行待ちです")
        return record

    async def issue_credentials(self, user):
        async with self.credential_lock(user.name):
            identity = self.credential_identity(user)
            record = self.store.get("credentials", user.name)
            if record and any(record.get(k) != v for k, v in identity.items()):
                await self._revoke_credentials(user.name, record)
                self.store.delete("credentials", user.name)
                record = None
            record = record or {**identity, "enabled": True, "state": "issuing"}
            if not record["enabled"]:
                return record
            if record.get("state") == "ready" and self.hub.valid(record, user):
                return record
            self.store.put("credentials", user.name, record)
            if not record.get("cf_token_id"):
                token = await self.cf.issue(
                    f"HPC {self.config.public_host} {user.name} {identity['uid']} {identity['hub_user_id']}"
                )
                record.update(
                    cf_token_id=token["id"],
                    client_id=token["client_id"],
                    client_secret=token["client_secret"],
                )
                self.store.put("credentials", user.name, record)
            if record.get("state") == "rotating_cloudflare":
                token = await self.cf.rotate(record["cf_token_id"])
                record.update(client_secret=token["client_secret"])
                record["state"] = "issuing"
                self.store.put("credentials", user.name, record)
            if not self.hub.valid(record, user) or record.get(
                "hub_token_id"
            ) in record.get("revoke_pending", []):
                # Revoke orphaned tokens from an interrupted issuance before creating another.
                for token in list(getattr(user, "api_tokens", [])):
                    if token.note == "HPC external API" and token.id != record.get(
                        "hub_token_id"
                    ):
                        self.hub.revoke(token.id)
                record.update(self.hub.issue(user))
                self.store.put("credentials", user.name, record)
            for token_id in record.get("revoke_pending", []):
                self.hub.revoke(token_id)
            record.pop("revoke_pending", None)
            record.update(state="ready", updated_at=time.time())
            self.store.put("credentials", user.name, record)
            return record

    async def rotate_credentials(self, user, kind):
        if kind not in {"cloudflare", "jupyterhub"}:
            raise ValueError("再発行対象が不正です")
        async with self.credential_lock(user.name):
            record = self.credential_record(user)
            if not record["enabled"] or record["state"] != "ready":
                raise ValueError(
                    "接続情報が準備できていません。管理者へ確認してください"
                )
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

    async def _revoke_credentials(self, username, record):
        record.update(enabled=False, state="revoking")
        self.store.put("credentials", username, record)
        self.hub.revoke(record.get("hub_token_id"))
        if hasattr(self.hub, "revoke_orphans"):
            self.hub.revoke_orphans(username)
        for token_id in record.get("revoke_pending", []):
            self.hub.revoke(token_id)
        if record.get("cf_token_id"):
            await self.cf.remove(record["cf_token_id"])
        record = {
            "uid": record["uid"],
            "hub_user_id": record["hub_user_id"],
            "enabled": False,
            "state": "disabled",
        }
        self.store.put("credentials", username, record)

    async def revoke_credentials(self, username):
        async with self.credential_lock(username):
            record = self.store.get("credentials", username)
            if record:
                await self._revoke_credentials(username, record)

    async def enable_credentials(self, user):
        async with self.credential_lock(user.name):
            record = self.store.get("credentials", user.name)
            if record and record.get("state") != "disabled":
                raise ValueError("失効処理が完了していません")
            self.store.delete("credentials", user.name)
        return await self.issue_credentials(user)

    def publication_lock(self, username):
        return self.publication_locks.setdefault(username, asyncio.Lock())

    def publication_key(self, username, name):
        return username + "/" + name

    def publication_url(self, record):
        return f"https://{self.config.public_host}/hub/user-api/{quote(record['username'], safe='')}/{record['name']}/"

    def get_publication(self, user, name):
        record = self.store.get("publications", self.publication_key(user.name, name))
        if (
            not record
            or record["uid"] != self.accounts.getpwnam(user.name).pw_uid
            or record["hub_user_id"] != user.id
        ):
            raise ValueError("API の登録が見つかりません")
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

    def publication_info(self, record):
        home = self.accounts.getpwnam(record["username"]).pw_dir
        target = public_candidate(record["target"], home)
        return {
            k: record.get(k)
            for k in ("name", "display_name", "state", "health_path", "checked_at")
        } | {
            "url": self.publication_url(record),
            "workdir": target["workdir"],
            "port": target["port"],
            "started_at": target["started_at"],
        }

    async def _probe_publication(self, record):
        await self.guard.check(record["target"])
        async with self.relay.request(
            self.inventory,
            record["target"],
            "GET" if record["health_path"] else "HEAD",
            record["health_path"] or "/",
            timeout=3,
        ) as response:
            if record["health_path"] and not 200 <= response.status < 300:
                raise ValueError("動作確認パスが正常な応答を返していません")

    async def publish_api(self, user, settings):
        async with self.publication_lock(user.name):
            key = self.publication_key(user.name, settings.name)
            old = self.store.get("publications", key)
            if old and (
                old["uid"] != self.accounts.getpwnam(user.name).pw_uid
                or old["hub_user_id"] != user.id
            ):
                raise ValueError(
                    "以前のアカウントの登録が残っています。管理者に確認してください"
                )
            if not old and len(self.list_publications(user)) >= self.config.max_apps:
                raise ValueError("API の登録数上限に達しています")
            credential = self.credential_record(user)
            if not credential.get("enabled") or credential.get("state") != "ready":
                raise ValueError("外部 API の接続情報が準備できていません")
            target = await asyncio.to_thread(
                self.inventory.choose,
                credential["uid"],
                settings.candidate,
                settings.port,
            )
            record = {
                **settings.model_dump(exclude={"candidate", "port"}),
                "username": user.name,
                "uid": credential["uid"],
                "hub_user_id": user.id,
                "target": target,
                "display_name": settings.display_name or target["display_name"],
                "state": "checking",
                "desired": "published",
                "remote_clean": False,
                "created_at": old["created_at"] if old else time.time(),
            }
            if old:
                record.update({k: old[k] for k in ("cf_app_id", "cf_aud") if k in old})
            # Save local deny before changing a target or invoking remote APIs.
            self.store.put("publications", key, record)
            return await self._publish_registration(record, credential)

    async def _publish_registration(self, record, credential):
        key = self.publication_key(record["username"], record["name"])
        record["state"] = "checking"
        record["remote_clean"] = False
        self.store.put("publications", key, record)
        try:
            await self.guard.protect(record["target"])
            await self._probe_publication(record)
            record["state"] = "configuring"
            self.store.put("publications", key, record)
            base = f"{self.config.public_host}/hub/user-api/{quote(record['username'], safe='')}/{record['name']}"
            app = await self.cf.app(
                f"HPC API / {self.config.public_host} / {record['username']} / {record['name']}",
                [base, base + "/*"],
                [credential["cf_token_id"]],
                record.get("cf_app_id"),
            )
            record.update(
                cf_app_id=app["id"], cf_aud=app["aud"], cf_checked_at=time.time()
            )
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

    async def operate_publication(self, user, name, action):
        async with self.publication_lock(user.name):
            record = self.get_publication(user, name)
            key = self.publication_key(user.name, name)
            if action == "publish":
                credential = self.credential_record(user)
                if not credential.get("enabled") or credential.get("state") != "ready":
                    raise ValueError("外部 API 接続が利用停止中です")
                record["desired"] = "published"
                return await self._publish_registration(record, credential)
            if action not in {"unpublish", "delete"}:
                raise ValueError("操作が不正です")
            record.update(
                desired="deleted" if action == "delete" else "unpublished",
                state="unpublished",
            )
            self.store.put("publications", key, record)
            await self._remove_remote_publication(record)
            if action == "delete":
                self.store.delete("publications", key)
            return record

    async def _remove_remote_publication(self, record):
        if record.get("remote_clean") and not record.get("cf_app_id"):
            return
        # Deterministic-name lookup also handles lost create responses.
        await self.cf.remove_app(
            f"HPC API / {self.config.public_host} / {record['username']} / {record['name']}",
            record.get("cf_app_id"),
        )
        record.pop("cf_app_id", None)
        record.pop("cf_aud", None)
        record["remote_clean"] = True
        self.store.put(
            "publications",
            self.publication_key(record["username"], record["name"]),
            record,
        )

    async def refresh_publication(self, record):
        async with self.publication_lock(record["username"]):
            key = self.publication_key(record["username"], record["name"])
            record = self.store.get("publications", key)
            if not record:
                return
            if record["desired"] != "published":
                await self._remove_remote_publication(record)
                if record["desired"] == "deleted":
                    self.store.delete("publications", key)
                return
            credential = self.store.get("credentials", record["username"])
            if (
                not credential
                or not credential.get("enabled")
                or credential.get("state") != "ready"
            ):
                record["state"] = "disabled"
            elif any(record[k] != credential[k] for k in ("uid", "hub_user_id")):
                record.update(state="unpublished", desired="deleted")
            else:
                try:
                    await asyncio.to_thread(self.inventory.validate, record["target"])
                except ValueError:
                    record["state"] = "disconnected"
                else:
                    if (
                        record["state"]
                        in {"checking", "configuring", "error", "disabled"}
                        or time.time() - record.get("cf_checked_at", 0) >= 300
                    ):
                        return await self._publish_registration(record, credential)
                    try:
                        await self._probe_publication(record)
                        record["state"] = "published"
                    except Exception:
                        record["state"] = "disconnected"
            record["checked_at"] = time.time()
            self.store.put("publications", key, record)

    async def unpublish_all(self, username):
        async with self.publication_lock(username):
            records = [
                self.store.get("publications", k)
                for k in self.store.names("publications")
                if k.startswith(username + "/")
            ]
            for record in records:
                record.update(desired="unpublished", state="unpublished")
                self.store.put(
                    "publications",
                    self.publication_key(username, record["name"]),
                    record,
                )
            for record in records:
                await self._remove_remote_publication(record)

    async def disable_user(self, username):
        # Local denial and Hub revocation precede remote operations, including retries.
        async with self.credential_lock(username):
            record = self.store.get("credentials", username)
            if record:
                record.update(enabled=False, state="revoking")
                self.store.put("credentials", username, record)
                self.hub.revoke(record.get("hub_token_id"))
        try:
            await self.unpublish_all(username)
        finally:
            await self.revoke_credentials(username)

    async def delete_user_records(self, username):
        async with self.credential_lock(username):
            record = self.store.get("credentials", username)
            if record and record.get("state") != "disabled":
                raise ValueError("資格情報の失効が未完了です")
            for key in self.store.names("publications"):
                if key.startswith(username + "/"):
                    await self._remove_remote_publication(
                        self.store.get("publications", key)
                    )
                    self.store.delete("publications", key)
            self.store.delete("credentials", username)

    async def synchronize(self):
        if self.sync_lock.locked():
            return
        async with self.sync_lock:
            rows = await asyncio.to_thread(self.users_snapshot)
            usernames = {row["username"] for row in rows}
            for name in self.store.names("credentials"):
                record = self.store.get("credentials", name)
                if name not in usernames or record.get("state") == "revoking":
                    try:
                        await self.disable_user(name)
                    except Exception:
                        log.warning("External API revocation pending for %s", name)
            for row in rows:
                try:
                    await self.issue_credentials(await self.hub.user(row["username"]))
                except Exception:
                    log.warning("External API issuance pending for %s", row["username"])
            for key in self.store.names("publications"):
                try:
                    await self.refresh_publication(self.store.get("publications", key))
                except Exception:
                    log.warning("External API publication recovery pending for %s", key)
            await self.guard.reconcile()

    async def list_ports(self, user):
        entry = self.accounts.getpwnam(user.name)
        return await asyncio.to_thread(self.inventory.ports, entry.pw_uid, entry.pw_dir)

    async def authorize_request(self, user, token, username, name, headers, body_size):
        if not token or not token.user or not user or user.name != username:
            raise UseCaseError("APIへのアクセスが許可されていません", "forbidden")
        try:
            record = self.credential_record(user)
        except (ValueError, KeyError):
            raise UseCaseError(
                "APIへのアクセスが許可されていません", "forbidden"
            ) from None
        expected_scope = f"custom:external-api:invoke!user={username}"
        if (
            not record.get("enabled")
            or record.get("state") != "ready"
            or token.id != record.get("hub_token_id")
            or expected_scope not in token.scopes
        ):
            raise UseCaseError("APIへのアクセスが許可されていません", "forbidden")
        # Rotation also invalidates JWTs minted with previous raw credentials.
        for header, key in (
            ("CF-Access-Client-ID", "client_id"),
            ("CF-Access-Client-Secret", "client_secret"),
        ):
            if not secrets.compare_digest(
                headers.get(header, "").encode(), record[key].encode()
            ):
                raise UseCaseError("APIへのアクセスが許可されていません", "forbidden")
        try:
            app = self.get_publication(user, name)
        except (ValueError, KeyError):
            raise UseCaseError("APIの登録が見つかりません", "missing") from None
        if app.get("state") != "published" or app.get("desired") != "published":
            raise UseCaseError("APIは公開されていません", "unavailable")
        try:
            await self.access.verify(
                headers.get("Cf-Access-Jwt-Assertion", ""), app, record
            )
        except ValueError:
            raise UseCaseError(
                "APIへのアクセスが許可されていません", "forbidden"
            ) from None
        if body_size > self.config.body_limit:
            raise UseCaseError("リクエスト本文が大きすぎます", "too_large")
        await self.guard.check(app["target"])
        return app
