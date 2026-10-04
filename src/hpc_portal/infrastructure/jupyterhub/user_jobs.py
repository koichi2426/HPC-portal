"""JupyterHubが管理する利用者ジョブへのアクセス。"""


class HubUserJobs:
    def __init__(self, app_factory):
        self.app_factory = app_factory

    def find_user(self, username):
        return self.app_factory().users.get(username)
