"""Service Tokenとは独立した、自作API専用Hubトークンの管理。"""

import time


class ManageHubCredentialsUseCase:
    def __init__(self, *, queries, store, hub_tokens, revoke_record):
        """本人照合・保存・Hub発行と旧所有者の失効処理を保持する。

        Args:
            queries: 本人の識別情報とユーザー単位のロック。
            store: 暗号化した認証情報の保存先。
            hub_tokens: 専用Hubトークンの発行・失効先。
            revoke_record: 所有者が変わった認証情報の失効操作。
        """
        self.queries = queries
        self.store = store
        self.hub_tokens = hub_tokens
        self.revoke_record = revoke_record

    async def execute(self, user, action="ensure"):
        """初回は自動発行し、本人の再発行・失効をService Tokenと独立に行う。

        Args:
            user: 操作対象のHubユーザー。
            action: ensure・rotate・revokeのいずれか。

        Returns:
            更新後の認証情報。明示的な失効はensureで復活させない。

        Raises:
            ValueError: 操作が不正、または管理者が利用を禁止している場合。
        """
        if action not in {"ensure", "rotate", "revoke"}:
            raise ValueError("Hubトークンの操作が不正です")

        async with self.queries.credential_lock(user.name):
            identity = self.queries.credential_identity(user)
            record = self.store.get("credentials", user.name)
            if record and any(
                record.get(key) != value for key, value in identity.items()
            ):
                await self.revoke_record.execute(user.name, record)
                self.store.delete("credentials", user.name)
                record = None

            record = record or {**identity, "enabled": True, "state": "unissued"}
            if (
                record["state"] == "issuing"
                and not record.get("cf_token_id")
                and not record.get("service_requested")
            ):
                # 移行前の自動発行待ちは、本人の発行依頼として引き継がない。
                record["state"] = "unissued"
                self.store.put("credentials", user.name, record)
            if not record["enabled"]:
                if action != "ensure":
                    raise ValueError("自作APIの利用は管理者が停止しています")
                return record
            if (
                action == "ensure"
                and record.get("hub_state") == "ready"
                and self.hub_tokens.valid(record, user)
                and not record.get("revoke_pending")
            ):
                return record
            if action == "ensure" and record.get("hub_state") == "revoked":
                if not record.get("hub_token_id") and not record.get("revoke_pending"):
                    return record
                action = "revoke"

            if action == "revoke":
                record["hub_state"] = "revoked"
                self.store.put("credentials", user.name, record)
                self.hub_tokens.revoke(record.pop("hub_token_id", None))
                record.pop("hub_token", None)
                self.hub_tokens.revoke_orphans(user.name)
                record.pop("revoke_pending", None)
            else:
                rotating = (
                    record.get("hub_state") == "rotating"
                    or record.get("state") == "rotating_jupyterhub"
                )
                if action == "rotate" and not rotating:
                    record.update(
                        hub_state="rotating",
                        revoke_pending=[record.get("hub_token_id")],
                    )
                    self.store.put("credentials", user.name, record)
                if not self.hub_tokens.valid(record, user) or record.get(
                    "hub_token_id"
                ) in record.get("revoke_pending", []):
                    self.hub_tokens.revoke_orphans(
                        user.name, keep_id=record.get("hub_token_id")
                    )
                    record.update(self.hub_tokens.issue(user))
                    self.store.put("credentials", user.name, record)
                for token_id in record.pop("revoke_pending", []):
                    self.hub_tokens.revoke(token_id)
                if record.get("state") == "rotating_jupyterhub":
                    record["state"] = (
                        "ready" if record.get("cf_token_id") else "unissued"
                    )
                record["hub_state"] = "ready"

            record["hub_updated_at"] = time.time()
            self.store.put("credentials", user.name, record)
            return record
