"""API公開先を表示するための情報に整える。"""

from urllib.parse import quote


def public_candidate(row, home):
    cwd = row["workdir"]
    if cwd == home or cwd.startswith(home.rstrip("/") + "/"):
        cwd = "~" + cwd[len(home) :]
    return {k: row[k] for k in ("candidate", "display_name", "port", "started_at")} | {
        "workdir": cwd,
        "state": "待ち受け中",
    }


class ApiPublicationPresenter:
    def __init__(self, config, accounts):
        self.config = config
        self.accounts = accounts

    def publication_url(self, record):
        return f"https://{self.config.public_host}/hub/user-api/{quote(record['username'], safe='')}/{record['name']}/"

    def publication_info(self, record):
        home = self.accounts.getpwnam(record["username"]).pw_dir
        target = public_candidate(record["target"], home)
        return {
            k: record.get(k)
            for k in ("name", "display_name", "state", "health_path", "checked_at")
        } | {
            "url": self.publication_url(record),
            "workdir": target["workdir"],
            "port": target["port"],
            "started_at": target["started_at"],
        }
