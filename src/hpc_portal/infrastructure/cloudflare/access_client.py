"""Cloudflare Accessを管理し、エラー本文を画面やログへ流さない。"""

import aiohttp


class CloudflareError(RuntimeError):
    """秘密値を含む応答本文を出さずに通知する、Access管理操作の失敗。"""

    pass


class CloudflareAccessClient:
    def __init__(self, config):
        """Cloudflare管理APIへ接続する設定を保持する。

        Args:
            config: CloudflareのアカウントID・管理キー・公開ドメインの設定。
        """
        self.config = config
        self.base = (
            f"https://api.cloudflare.com/client/v4/accounts/{config.account_id}/access"
        )

    async def request(self, method, path, data=None, *, allow_missing=False):
        """Cloudflare管理APIへ接続し、エラー本文を公開せず結果だけを返す。

        Args:
            method: 使用するHTTPメソッド。
            path: アカウント配下の管理APIの相対パス。
            data: 送信するJSONデータ。
            allow_missing: 404応答を未作成として許容する場合はTrue。

        Returns:
            Cloudflare応答のresult。404を許容した場合はNone。

        Raises:
            CloudflareError: 管理APIへの接続・更新に失敗した場合、または管理対象を一意に確認できない場合。
        """
        # 認証キーを別の接続先へ送らないよう、管理APIのリダイレクトは追わない。
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
        """ページ単位の管理API応答をまとめて一覧を取得する。

        Args:
            path: アカウント配下の管理APIの相対パス。

        Returns:
            全ページのレコード一覧。

        Raises:
            CloudflareError: 管理APIへの接続・更新に失敗した場合、または管理対象を一意に確認できない場合。
        """
        rows = []
        for page in range(1, 1001):
            batch = await self.request("GET", f"{path}?page={page}&per_page=100")
            rows.extend(batch)
            if len(batch) < 100:
                return rows
        raise CloudflareError("Cloudflare の一覧を取得できません")

    async def issue(self, name):
        """管理名に対応するService Tokenを発行し、未確認の発行は再発行で回復する。

        Args:
            name: ポータルが管理対象を照合する一意の管理名。

        Returns:
            ID・Client ID・Client Secretを含む発行結果。

        Raises:
            CloudflareError: 管理APIへの接続・更新に失敗した場合、または管理対象を一意に確認できない場合。
        """
        rows = await self.listing("/service_tokens")
        matches = [row for row in rows if row.get("name") == name]
        if len(matches) > 1:
            raise CloudflareError("重複した管理トークンを確認してください")
        if matches:
            # 作成応答を失って秘密値が分からない場合、同名トークンを再発行して回復する。
            return await self.rotate(matches[0]["id"])

        if len(rows) >= self.config.token_limit:
            raise CloudflareError("Service Token の発行上限に達しています")

        return await self.request(
            "POST", "/service_tokens", {"name": name, "duration": "forever"}
        )

    async def rotate(self, token_id):
        """既存Service Tokenの秘密値を再発行する。

        Args:
            token_id: 更新・失効させるトークンの識別子。

        Returns:
            新しいClient Secretを含む再発行結果。

        Raises:
            CloudflareError: 管理APIへの接続・更新に失敗した場合、または管理対象を一意に確認できない場合。
        """
        return await self.request("POST", f"/service_tokens/{token_id}/rotate", {})

    async def remove(self, token_id):
        """Service Tokenを削除し、既に存在しない場合も完了として扱う。

        Args:
            token_id: 更新・失効させるトークンの識別子。

        Raises:
            CloudflareError: 管理APIへの接続・更新に失敗した場合、または管理対象を一意に確認できない場合。
        """
        await self.request("DELETE", f"/service_tokens/{token_id}", allow_missing=True)

    async def app(self, name, paths, token_ids, existing_id=None):
        """本人のService Tokenだけを許可するAccessアプリを作成・更新する。

        Args:
            name: ポータルが管理対象を照合する一意の管理名。
            paths: Accessで保護する公開ホスト名とパスの一覧。
            token_ids: 公開先への接続を許可するService TokenのID一覧。
            existing_id: 更新・削除対象の既存AccessアプリID。未指定時は管理名で検索する。

        Returns:
            作成・更新したAccessアプリのidとaud。

        Raises:
            CloudflareError: 管理APIへの接続・更新に失敗した場合、または管理対象を一意に確認できない場合。
        """
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

        # ブラウザのAllow条件を加えず、指定されたService Tokenだけを許可する。
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
        """保存IDまたは管理名でAccessアプリを照合して削除する。

        Args:
            name: ポータルが管理対象を照合する一意の管理名。
            existing_id: 更新・削除対象の既存AccessアプリID。未指定時は管理名で検索する。

        Raises:
            CloudflareError: 管理APIへの接続・更新に失敗した場合、または管理対象を一意に確認できない場合。
        """
        if existing_id:
            row = await self.request("GET", f"/apps/{existing_id}", allow_missing=True)
            if row is not None:
                if row.get("name") != name:
                    raise CloudflareError("管理 Access アプリの所有を確認してください")
                await self.request("DELETE", f"/apps/{existing_id}", allow_missing=True)
                return

        # 作成応答や保存IDが失われた場合も、管理名で探して削除を再試行する。
        matches = [r for r in await self.listing("/apps") if r.get("name") == name]
        if len(matches) > 1:
            raise CloudflareError("重複した管理 Access アプリを確認してください")
        for row in matches:
            await self.request("DELETE", f"/apps/{row['id']}", allow_missing=True)
