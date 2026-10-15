"""個人別SSHトークンと、共通AccessアプリのService Auth許可を管理する。"""

import asyncio
import logging
import time

log = logging.getLogger("jupyterhub.ssh-access")


class SshAccessUseCase:
    def __init__(self, *, config, store, cloudflare, accounts, hub_users):
        """保存・Cloudflare更新・本人照合と、共有アプリの排他制御を準備する。

        Args:
            config: SSH公開ホストと管理設定。
            store: 暗号化したSSHレコードの保存先。
            cloudflare: Service TokenとAccessアプリの管理先。
            accounts: Linuxユーザーの識別情報と一覧取得先。
            hub_users: 同名アカウントの再作成を区別するHubユーザー取得先。
        """
        self.config = config
        self.store = store
        self.cloudflare = cloudflare
        self.accounts = accounts
        self.hub_users = hub_users
        self.lock = asyncio.Lock()

    def identity(self, user):
        """現在のLinux UIDとHub IDを照合用に取得する。

        Args:
            user: 操作対象のHubユーザー。

        Returns:
            同名ユーザーの再作成を区別する識別情報。
        """
        return {"uid": self.accounts.getpwnam(user.name).pw_uid, "hub_user_id": user.id}

    def record(self, user):
        """外部設定を変更せず、本人の発行状態を取得する。

        Args:
            user: 操作対象のHubユーザー。

        Returns:
            本人の保存レコード、または未発行状態。

        Raises:
            ValueError: 以前の所有者のレコードが残っている場合。
        """
        identity = self.identity(user)
        record = self.store.get("ssh_credentials", user.name)
        if record and any(record.get(key) != value for key, value in identity.items()):
            raise ValueError("以前のユーザーの失効処理が必要です")
        return record or {**identity, "enabled": True, "state": "unissued"}

    async def reconcile(self):
        """ロック内で全利用者の希望状態を共有Accessアプリへ反映する。"""
        app = self.store.get("ssh_application", self.config.public_host) or {}
        token_ids = [
            record["cf_token_id"]
            for name in self.store.names("ssh_credentials")
            if (record := self.store.get("ssh_credentials", name)).get("enabled")
            and record.get("state") in {"issuing", "ready", "rotating"}
            and record.get("cf_token_id")
        ]
        result = await self.cloudflare.app(
            f"HPC SSH {self.config.public_host}",
            [self.config.public_host],
            token_ids,
            app.get("id"),
        )
        self.store.put("ssh_application", self.config.public_host, result)

    async def execute(self, user, action):
        """本人の発行・再発行・失効と共有ポリシー更新を直列化する。

        Args:
            user: 操作対象のHubユーザー。
            action: issue・rotate・revokeのいずれか。

        Returns:
            操作後の本人のSSHレコード。

        Raises:
            ValueError: 管理者が禁止している、操作が不正、または他の処理が未完了の場合。
        """
        if action not in {"issue", "rotate", "revoke"}:
            raise ValueError("SSHトークンの操作が不正です")
        async with self.lock:
            record = self.record(user)
            if not record["enabled"]:
                raise ValueError("SSH公開の利用は管理者が停止しています")
            if action == "revoke":
                return await self.revoke(user.name, record)
            if record["state"] == "revoking":
                raise ValueError("失効処理が完了していません")
            if action == "rotate":
                if record["state"] != "ready":
                    raise ValueError("発行済みトークンの更新だけが可能です")
                record["state"] = "rotating"
            elif record["state"] == "ready":
                return record
            elif record["state"] in {"unissued", "issuing"}:
                record["state"] = "issuing"
            else:
                raise ValueError("更新処理の完了を待ってください")
            self.store.put("ssh_credentials", user.name, record)
            return await self.issue(user.name, record)

    async def issue(self, username, record):
        """発行の各工程を保存し、共有ポリシー反映後に利用可能へ進める。

        Args:
            username: 対象のLinuxユーザー名。
            record: 発行依頼済みの本人レコード。

        Returns:
            Accessへの登録が完了した認証情報。
        """
        if not record.get("cf_token_id"):
            token = await self.cloudflare.issue(
                f"HPC SSH {self.config.public_host} {username} {record['uid']} {record['hub_user_id']}"
            )
            record.update(
                cf_token_id=token["id"],
                client_id=token["client_id"],
                client_secret=token["client_secret"],
            )
            self.store.put("ssh_credentials", username, record)
        if record["state"] == "rotating":
            token = await self.cloudflare.rotate(record["cf_token_id"])
            record.update(client_secret=token["client_secret"], state="issuing")
            self.store.put("ssh_credentials", username, record)
        await self.reconcile()
        record.update(state="ready", updated_at=time.time())
        self.store.put("ssh_credentials", username, record)
        return record

    async def revoke(self, username, record):
        """許可対象から外し、リモート失効を確認して秘密値を破棄する。

        Args:
            username: 対象のLinuxユーザー名。
            record: 失効対象の保存レコード。

        Returns:
            秘密値を除いた失効済みレコード。
        """
        record["state"] = "revoking"
        self.store.put("ssh_credentials", username, record)
        if record.get("cf_token_id"):
            await self.cloudflare.remove(record["cf_token_id"])
        await self.reconcile()
        for key in ("cf_token_id", "client_id", "client_secret"):
            record.pop(key, None)
        record.update(
            state="unissued" if record["enabled"] else "disabled",
            updated_at=time.time(),
        )
        self.store.put("ssh_credentials", username, record)
        return record

    async def set_allowed(self, username, enabled):
        """管理者の利用許可を切り替え、禁止時はSSHトークンも失効する。

        Args:
            username: 対象のLinuxユーザー名。
            enabled: 新しい利用許可。

        Raises:
            ValueError: 失効処理の完了前に再び許可しようとした場合。
        """
        user = await self.hub_users.user(username)
        async with self.lock:
            record = self.record(user)
            if enabled and record["state"] == "revoking":
                raise ValueError("失効処理が完了していません")
            record["enabled"] = enabled
            self.store.put("ssh_credentials", username, record)
            if enabled:
                record["state"] = (
                    "unissued" if record["state"] == "disabled" else record["state"]
                )
                self.store.put("ssh_credentials", username, record)
            else:
                await self.revoke(username, record)

    async def delete_user(self, username):
        """ユーザー削除前にSSHを失効し、削除完了まで再発行を禁止する。

        Args:
            username: 対象のLinuxユーザー名。
        """
        async with self.lock:
            record = self.store.get("ssh_credentials", username)
            if record:
                record["enabled"] = False
                await self.revoke(username, record)

    async def forget_user(self, username):
        """Linuxユーザー削除後、失効済みのSSH記録を除く。

        Args:
            username: 削除したLinuxユーザー名。

        Raises:
            ValueError: SSH資格情報の失効が未完了の場合。
        """
        async with self.lock:
            record = self.store.get("ssh_credentials", username)
            if record and record["state"] != "disabled":
                raise ValueError("SSH資格情報の失効が未完了です")
            self.store.delete("ssh_credentials", username)

    async def synchronize(self):
        """依頼済み処理と削除アカウントだけを復旧し、未発行の人には発行しない。"""
        async with self.lock:
            rows = await asyncio.to_thread(self.accounts.linux_users_snapshot)
            names = {row["username"] for row in rows}
            for name in self.store.names("ssh_credentials"):
                record = self.store.get("ssh_credentials", name)
                try:
                    if name not in names:
                        record["enabled"] = False
                        await self.revoke(name, record)
                        self.store.delete("ssh_credentials", name)
                        continue
                    user = await self.hub_users.user(name)
                    if any(
                        record.get(key) != value
                        for key, value in self.identity(user).items()
                    ):
                        record["enabled"] = False
                        await self.revoke(name, record)
                        self.store.delete("ssh_credentials", name)
                    elif record["state"] == "revoking":
                        await self.revoke(name, record)
                    elif record.get("enabled") and record["state"] in {
                        "issuing",
                        "rotating",
                    }:
                        await self.issue(name, record)
                except Exception:
                    log.warning("SSH credential recovery pending for %s", name)
            await self.reconcile()
