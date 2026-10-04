"""JupyterHubが管理する利用者ジョブへのアクセス。"""


class HubUserJobs:
    def __init__(self, app_factory):
        self.app_factory = app_factory

    def active_servers(self, username, application):
        user = self.app_factory().users.get(username)
        if user is None:
            return []
        return [
            name
            for name, spawner in list(user.spawners.items())
            if str(
                (getattr(spawner, "user_options", None) or {}).get("app_choice") or ""
            )
            == application
            and (getattr(spawner, "active", False) or getattr(spawner, "pending", None))
        ]

    async def stop_server(self, username, server_name):
        user = self.app_factory().users.get(username)
        if user is not None:
            await user.stop(server_name)
