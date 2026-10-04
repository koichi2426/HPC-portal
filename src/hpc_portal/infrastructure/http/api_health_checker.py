"""公開予定のAPIが現在のプロセスから正常に応答するかを確認する。"""


class ApiHealthChecker:
    def __init__(self, guard, inventory, relay):
        self.guard = guard
        self.inventory = inventory
        self.relay = relay

    async def check(self, record):
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
