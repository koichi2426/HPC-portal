"""Cloudflare管理APIの共通接続。応答本文や秘密値をエラーへ含めない。"""

import aiohttp


class CloudflareError(RuntimeError):
    """秘密値を含む応答本文を出さずに通知する、Access管理操作の失敗。"""

    pass


class CloudflareApiClient:
    def __init__(self, api_token):
        """Cloudflareだけへ送る管理用資格情報を保持する。

        Args:
            api_token: 管理APIへ接続するBearerトークン。
        """
        self.api_token = api_token
        self.base = "https://api.cloudflare.com/client/v4"

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
                    headers={"Authorization": "Bearer " + self.api_token},
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
