"""localhost上で本番の画面を表示する開発サーバー。"""

import argparse
import hashlib
import logging
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, PrefixLoader
from jupyterhub.app import JupyterHub
from tornado import autoreload, ioloop, web

from dev.preview.handlers import ControlsHandler, MockApiHandler, PageHandler
from dev.preview.mock_data import MockState

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = REPOSITORY_ROOT / "frontend"
LOGGER = logging.getLogger(__name__)
PREVIEW_TIMEZONE = ZoneInfo("Asia/Tokyo")


class PreviewLogFormatter(logging.Formatter):
    """開発ログに日本時間を付け、変更検知の前に空行を入れる。"""

    def formatTime(self, record, datefmt=None):
        """実行環境のタイムゾーンに依存せず、ログの時刻を整形する。

        Args:
            record: 出力するログの記録。
            datefmt: 時刻の表示形式。

        Returns:
            日本時間で整形した時刻。
        """
        timestamp = datetime.fromtimestamp(record.created, PREVIEW_TIMEZONE)
        return timestamp.strftime(datefmt or "%H:%M:%S")

    def format(self, record):
        """画面ファイルとTornadoの再起動ログを、空行で区切る。

        Args:
            record: 出力するログの記録。

        Returns:
            時刻と必要な区切りを付けたログ文字列。
        """
        message = super().format(record)
        source_changed = (
            record.name == "tornado.general"
            and record.msg == "%s modified; restarting server"
        )
        if source_changed or getattr(record, "preview_change", False):
            return "\n" + message
        return message


def configure_logging():
    """起動・停止・変更検知のログに、共通の時刻表示を設定する。"""
    handler = logging.StreamHandler()
    handler.setFormatter(
        PreviewLogFormatter("[%(asctime)s] %(levelname)s: %(message)s")
    )
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    logging.getLogger("tornado.access").setLevel(logging.WARNING)


def frontend_file_versions():
    """画面ファイルの更新・追加・削除を検知するための情報を取得する。

    Returns:
        HTML・CSS・JavaScriptのパスと、更新時刻・サイズ・inodeの辞書。
    """
    versions = {}
    roots = (FRONTEND_ROOT, Path(__file__).parent / "templates")
    for root in roots:
        for path in root.rglob("*"):
            if path.suffix not in {".html", ".css", ".js"} or not path.is_file():
                continue
            try:
                stat = path.stat()
            except FileNotFoundError:
                # エディターが保存中にファイルを置き換えた場合は、次の巡回で検知する。
                continue
            versions[path] = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
    return versions


def watch_frontend_changes():
    """画面ファイルの変更を記録する。モック状態を保つため、再起動は行わない。

    Returns:
        起動済みの監視コールバック。サーバー停止時に停止する。
    """
    previous = frontend_file_versions()

    def log_changes():
        """前回との差分をまとめ、変更がある場合だけログを出す。"""
        nonlocal previous
        current = frontend_file_versions()
        changed = sorted(
            path
            for path in previous.keys() | current.keys()
            if previous.get(path) != current.get(path)
        )
        previous = current
        if changed:
            names = ", ".join(
                str(path.relative_to(REPOSITORY_ROOT)) for path in changed
            )
            LOGGER.info(
                "画面ファイルの変更を検知: %s（ブラウザを再読み込みして反映）",
                names,
                extra={"preview_change": True},
            )

    watcher = ioloop.PeriodicCallback(log_changes, 1000)
    watcher.start()
    return watcher


def watch_source_tree():
    """未読込のsrcモジュールやファイルの追加・削除も、サーバー再起動の対象にする。"""
    source_root = REPOSITORY_ROOT / "src"
    autoreload.watch(str(source_root))

    # ディレクトリも監視し、既存ファイルの更新だけでなく追加・削除を検知する。
    for path in source_root.rglob("*"):
        if "__pycache__" in path.parts:
            continue
        if path.is_dir() or path.suffix == ".py":
            autoreload.watch(str(path))


def portal_css():
    """Ansibleと同じファイル順でCSSを組み立て、毎回最新の内容を返す。"""
    files = sorted(
        path
        for path in (FRONTEND_ROOT / "static/css").glob("*.css")
        if re.fullmatch(r"[0-9]+-.*\.css", path.name)
    )
    fragments = [path.read_bytes() for path in files]
    return b"".join(
        content if content.endswith(b"\n") else content + b"\n" for content in fragments
    )


class AssetVersions:
    def __getitem__(self, name):
        content = (
            portal_css()
            if name == "portal_css"
            else (FRONTEND_ROOT / "static" / name).read_bytes()
        )
        return hashlib.sha256(content).hexdigest()[:12]


class CssHandler(web.RequestHandler):
    def get(self):
        self.set_header("Content-Type", "text/css; charset=UTF-8")
        self.set_header("Cache-Control", "no-store")
        self.finish(portal_css())


def create_application(state=None, *, autoreload=False):
    # パッケージのテンプレート配置だけを参照する。Hub本体は初期化・起動しない。
    hub_templates = Path(JupyterHub()._template_paths_default()[0])
    hub_static = hub_templates.parent / "static"
    environment = Environment(
        loader=ChoiceLoader(
            [
                PrefixLoader(
                    {
                        "templates": FileSystemLoader(hub_templates),
                        "preview": FileSystemLoader(
                            Path(__file__).parent / "templates"
                        ),
                    },
                    "/",
                ),
                FileSystemLoader([FRONTEND_ROOT / "templates", hub_templates]),
            ]
        ),
        autoescape=True,
        auto_reload=True,
    )
    routes = [
        (r"/", web.RedirectHandler, {"url": "/hub/home"}),
        (r"/hub/?", web.RedirectHandler, {"url": "/hub/home"}),
        (
            r"/hub/logo",
            web.RedirectHandler,
            {"url": "/hub/static/images/jupyterhub-80.png"},
        ),
        (r"/hub/static/(.*)", web.StaticFileHandler, {"path": str(hub_static)}),
        (
            r"/hub/hpc-js/([a-z0-9-]+\.js)",
            web.StaticFileHandler,
            {"path": str(FRONTEND_ROOT / "static/js")},
        ),
        (r"/hub/hpc-portal\.css", CssHandler),
        (r"/hub/preview/settings", ControlsHandler),
        (r"/hub/preview/app/([^/]+)", PageHandler, {"page": "jump"}),
    ]
    for path, page in {
        "home": "home",
        "new": "new",
        "external-api": "external_api",
        "api-publications": "publications",
        "ssh-access": "ssh",
        "llm-api": "llm_api",
        "account/password": "password",
        "admin": "admin",
        "admin/users": "admin_users",
        "login": "login",
        "logout": "logout",
        "token": "token",
    }.items():
        routes.append((f"/hub/{path}", PageHandler, {"page": page}))
    routes.extend(
        [
            (r"/hub/spawn/([^/]+)(?:/([^/]+))?", PageHandler, {"page": "new"}),
            (r"/hub/apps/([^/]+)/version", MockApiHandler, {"kind": "version"}),
            (r"/hub/apps/([^/]+)", PageHandler, {"page": "app"}),
            (r"/hub/api/user", MockApiHandler, {"kind": "user"}),
            (
                r"/hub/api/users/([^/]+)/tokens(?:/([^/]+))?",
                MockApiHandler,
                {"kind": "hub_token"},
            ),
            (
                r"/hub/api/users/([^/]+)/(?:server|servers/([^/]+))",
                MockApiHandler,
                {"kind": "server"},
            ),
        ]
    )
    for path, kind in {
        "hpc-resource-status": "resources",
        "hpc-app-memory": "memory",
        "admin/apps/api": "admin_apps",
        "admin/users/api": "admin_users",
        "external-api/credentials": "credentials",
        "ssh-access/credentials": "ssh_credentials",
        "api-publications/ports": "ports",
        "api-publications/api": "publications",
        "llm-api/api": "llm",
        "account/password/api": "password",
    }.items():
        routes.append((f"/hub/{path}", MockApiHandler, {"kind": kind}))
    return web.Application(
        routes,
        mock_state=state if state is not None else MockState(),
        jinja_environment=environment,
        asset_versions=AssetVersions(),
        xsrf_cookies=True,
        autoreload=autoreload,
        compiled_template_cache=False,
        static_hash_cache=False,
    )


def main():
    """モックサーバーと変更監視を起動し、停止時に後処理する。"""
    parser = argparse.ArgumentParser(description="HPC-portalの画面確認用モックサーバー")
    parser.add_argument(
        "--port", type=int, default=8001, help="待ち受けポート（既定: 8001）"
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("ポートは1〜65535で指定してください")
    configure_logging()
    watch_source_tree()
    application = create_application(autoreload=True)
    try:
        server = application.listen(args.port, address="127.0.0.1")
    except OSError as exc:
        parser.exit(
            1,
            f"起動できません: {exc}\n別のポートを指定してください: make dev PORT=8002\n",
        )
    watcher = watch_frontend_changes()
    LOGGER.info("開発サーバーを起動しました: http://localhost:%s/hub/home", args.port)
    LOGGER.info("モック環境 / 停止: Ctrl+C")
    try:
        ioloop.IOLoop.current().start()
    except KeyboardInterrupt:
        LOGGER.info("開発サーバーを停止しました。")
    finally:
        watcher.stop()
        server.stop()
        ioloop.IOLoop.current().stop()


if __name__ == "__main__":
    main()
