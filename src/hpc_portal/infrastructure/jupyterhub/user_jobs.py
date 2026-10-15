"""JupyterHubが管理する利用者ジョブへのアクセス。"""


class HubUserJobs:
    def __init__(self, app_factory):
        """現在のJupyterHubを取得する関数を保持する。

        Args:
            app_factory: 現在のJupyterHubインスタンスを返す関数。
        """
        self.app_factory = app_factory

    def active_servers(self, username, application):
        """指定ユーザー・アプリに該当する稼働中または起動中のserver名を取得する。

        Args:
            username: 対象のLinuxユーザー名。
            application: 対象アプリの識別名。

        Returns:
            該当するnamed server名の一覧。ユーザー未登録なら空の一覧。
        """
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
        """指定ユーザーのnamed serverを停止する。未登録ユーザーは処理しない。

        Args:
            username: 対象のLinuxユーザー名。
            server_name: 対象のnamed server名。空文字列はデフォルトserverを表す。
        """
        user = self.app_factory().users.get(username)
        if user is not None:
            await user.stop(server_name)
