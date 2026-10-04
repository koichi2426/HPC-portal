"""Small account-scoped Cloudflare client. Error bodies never reach logs/UI."""

import aiohttp


class CloudflareError(RuntimeError):
    pass


class CloudflareAccessClient:
    def __init__(self, config):
        self.config = config
        self.base = (
            f"https://api.cloudflare.com/client/v4/accounts/{config.account_id}/access"
        )

    async def request(self, method, path, data=None, *, allow_missing=False):
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            ) as session:
                async with session.request(
                    method,
                    self.base + path,
                    json=data,
                    headers={"Authorization": "Bearer " + self.config.api_token},
                    allow_redirects=False,
                ) as response:
                    if response.status == 404 and allow_missing:
                        return None
                    if response.status >= 300:
                        raise CloudflareError(
                            f"Cloudflare の設定に失敗しました (HTTP {response.status})"
                        )
                    body = await response.json()
                    if not body.get("success"):
                        raise CloudflareError(
                            "Cloudflare が設定変更を受け付けませんでした"
                        )
                    return body.get("result")
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise CloudflareError(
                "Cloudflare の応答を確認できません。再試行してください"
            ) from exc

    async def listing(self, path):
        rows = []
        for page in range(1, 1001):
            batch = await self.request("GET", f"{path}?page={page}&per_page=100")
            rows.extend(batch)
            if len(batch) < 100:
                return rows
        raise CloudflareError("Cloudflare の一覧を取得できません")

    async def issue(self, name):
        rows = await self.listing("/service_tokens")
        matches = [row for row in rows if row.get("name") == name]
        if len(matches) > 1:
            raise CloudflareError("重複した管理トークンを確認してください")
        if matches:
            # Recover a create whose response/secret was lost. Never reuse an unknown secret.
            return await self.rotate(matches[0]["id"])
        if len(rows) >= self.config.token_limit:
            raise CloudflareError("Service Token の発行上限に達しています")
        return await self.request(
            "POST", "/service_tokens", {"name": name, "duration": "forever"}
        )

    async def rotate(self, token_id):
        return await self.request("POST", f"/service_tokens/{token_id}/rotate", {})

    async def remove(self, token_id):
        await self.request("DELETE", f"/service_tokens/{token_id}", allow_missing=True)

    async def app(self, name, paths, token_ids, existing_id=None):
        if not existing_id:
            matches = [
                row for row in await self.listing("/apps") if row.get("name") == name
            ]
            if len(matches) > 1:
                raise CloudflareError("重複した管理 Access アプリを確認してください")
            existing_id = matches[0]["id"] if matches else None
        policies = []
        policy_id = None
        if existing_id:
            existing = await self.request(
                "GET", f"/apps/{existing_id}", allow_missing=True
            )
            if existing is None:
                existing_id = None
            else:
                if existing.get("name") != name:
                    raise CloudflareError("管理 Access アプリの ID を確認してください")
                matches = [
                    policy
                    for policy in existing.get("policies", [])
                    if policy.get("name") == "HPC service credentials"
                ]
                if matches:
                    policy_id = matches[0]["id"]
        if token_ids:
            policies.insert(
                0,
                {
                    "name": "HPC service credentials",
                    "decision": "non_identity",
                    "precedence": 1,
                    "include": [
                        {"service_token": {"token_id": token_id}}
                        for token_id in token_ids
                    ],
                },
            )
            if policy_id:
                policies[0]["id"] = policy_id
        payload = {
            "name": name,
            "type": "self_hosted",
            "domain": paths[0],
            "destinations": [{"type": "public", "uri": path} for path in paths],
            "policies": policies,
            "app_launcher_visible": False,
        }
        method, path = (
            ("PUT", f"/apps/{existing_id}") if existing_id else ("POST", "/apps")
        )
        result = await self.request(method, path, payload)
        if not result.get("id") or not result.get("aud"):
            raise CloudflareError("Access アプリの認証情報を確認できません")
        return {"id": result["id"], "aud": result["aud"]}

    async def remove_app(self, name, existing_id=None):
        if existing_id:
            row = await self.request("GET", f"/apps/{existing_id}", allow_missing=True)
            if row is not None:
                if row.get("name") != name:
                    raise CloudflareError("管理 Access アプリの所有を確認してください")
                await self.request("DELETE", f"/apps/{existing_id}", allow_missing=True)
                return
        # Recover an uncertain create, including an old ID missing in the account.
        matches = [r for r in await self.listing("/apps") if r.get("name") == name]
        if len(matches) > 1:
            raise CloudflareError("重複した管理 Access アプリを確認してください")
        for row in matches:
            await self.request("DELETE", f"/apps/{row['id']}", allow_missing=True)
