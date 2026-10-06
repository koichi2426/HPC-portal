"""API公開先を表示するための情報に整える。"""

from urllib.parse import quote


def public_candidate(row, home):
    """内部のプロセス識別情報を除き、候補名とホーム基準の作業パスを返す。

    Args:
        row: プロセス名・開始時刻・作業パスを含む待受候補。
        home: ユーザーのホームディレクトリ。

    Returns:
        候補ID・表示名・ポート・開始時刻・作業パス・待受状態の辞書。
    """
    cwd = row["workdir"]
    if cwd == home or cwd.startswith(home.rstrip("/") + "/"):
        cwd = "~" + cwd[len(home) :]
    return {k: row[k] for k in ("candidate", "display_name", "port", "started_at")} | {
        "workdir": cwd,
        "state": "待ち受け中",
    }


class ApiPublicationPresenter:
    def __init__(self, config, accounts):
        """公開URLとホーム基準の表示情報を作るための依存を保持する。

        Args:
            config: 外部APIの公開ドメイン・接続制限・保存先などの設定。
            accounts: Linuxユーザーの照合・作成・変更を行う接続先。
        """
        self.config = config
        self.accounts = accounts

    def publication_url(self, record):
        """ユーザー名をURLエンコードし、登録APIの公開URLを組み立てる。

        Args:
            record: 公開URLと実行情報の表示に使うAPI公開レコード。

        Returns:
            登録APIのHTTPS公開URL。
        """
        return f"https://{self.config.public_host}/hub/user-api/{quote(record['username'], safe='')}/{record['name']}/"

    def publication_info(self, record):
        """公開URLとホーム基準の作業パスを含む、画面向けの公開情報を作る。

        Args:
            record: 公開URLと実行情報の表示に使うAPI公開レコード。

        Returns:
            API名・表示名・公開状態・公開URL・実行情報の辞書。
        """
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
