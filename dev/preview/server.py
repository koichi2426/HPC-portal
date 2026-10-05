"""localhost上で本番の画面を表示する開発サーバー。"""

import argparse
import hashlib
import logging
import re
from pathlib import Path

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, PrefixLoader
from jupyterhub.app import JupyterHub
from tornado import ioloop, web

from dev.preview.handlers import ControlsHandler, MockApiHandler, PageHandler
from dev.preview.mock_data import MockState

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = REPOSITORY_ROOT / "frontend"


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
    parser = argparse.ArgumentParser(description="HPC-portalの画面確認用モックサーバー")
    parser.add_argument(
        "--port", type=int, default=8001, help="待ち受けポート（既定: 8001）"
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("ポートは1〜65535で指定してください")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    logging.getLogger("tornado.access").setLevel(logging.WARNING)
    application = create_application(autoreload=True)
    try:
        server = application.listen(args.port, address="127.0.0.1")
    except OSError as exc:
        parser.exit(
            1,
            f"起動できません: {exc}\n別のポートを指定してください: make dev PORT=8002\n",
        )
    print(
        f"画面確認: http://localhost:{args.port}/hub/home\nモック環境 / 停止: Ctrl+C",
        flush=True,
    )
    try:
        ioloop.IOLoop.current().start()
    except KeyboardInterrupt:
        print("\n開発サーバーを停止しました。", flush=True)
    finally:
        server.stop()
        ioloop.IOLoop.current().stop()


if __name__ == "__main__":
    main()
