"""公開予定のAPIが現在のプロセスから正常に応答するかを確認する。"""


class ApiHealthChecker:
    def __init__(self, guard, inventory, relay):
        """公開先の保護確認・同一性確認・HTTP接続を受け取る。

        Args:
            guard: 公開先ポートの保護状態を設定・確認する接続先。
            inventory: 公開対象の待受プロセスが登録時と同一か確認する接続先。
            relay: プロセスの同一性を確認してHTTPを転送する接続先。
        """
        self.guard = guard
        self.inventory = inventory
        self.relay = relay

    async def check(self, record):
        """ポート保護と接続先を確認し、指定した確認パスの応答を検証する。

        Args:
            record: 接続先と動作確認パスを含むAPI公開の保存レコード。

        Raises:
            ValueError: 保護や接続先を確認できない場合、または確認パスが成功応答を返さない場合。
        """
        await self.guard.check(record["target"])
        async with self.relay.request(
            self.inventory,
            record["target"],
            "GET" if record["health_path"] else "HEAD",
            record["health_path"] or "/",
            timeout=3,
        ) as response:
            if record["health_path"] and (not 200 <= response.status < 300):
                raise ValueError("動作確認パスが正常な応答を返していません")
