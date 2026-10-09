"""本番の画面へモック状態を渡すHTTPハンドラー。"""

import json
import time
from types import SimpleNamespace
from urllib.parse import unquote, urlencode

from pydantic import ValidationError
from tornado import web

from dev.preview.mock_data import FORM_SETTINGS, SCENARIOS
from hpc_portal.presentation.job_form_renderer import render_options_form
from hpc_portal.presentation.schemas.requests.admin_users import HpcAdminUsersRequest
from hpc_portal.presentation.schemas.requests.external_api import (
    Operation,
    Registration,
)
from hpc_portal.presentation.schemas.requests.password import HpcPasswordChangeRequest


class PreviewHandler(web.RequestHandler):
    @property
    def state(self):
        return self.settings["mock_state"]

    def get_current_user(self):
        name = self.get_cookie("preview_user", "alice")
        return self.state.user(name if name in self.state.users else "alice")

    def set_default_headers(self):
        self.set_header("Cache-Control", "no-store")

    def require_admin(self):
        if not self.current_user.admin:
            raise web.HTTPError(403, reason="管理者のみアクセスできます")

    def require_external_api(self):
        if not self.state.accounts[self.current_user.name]["external_api_enabled"]:
            raise web.HTTPError(403, reason="自作API公開は利用停止中です")

    def json_body(self):
        try:
            data = json.loads(self.request.body)
            if not isinstance(data, dict):
                raise ValueError
            return data
        except ValueError:
            raise web.HTTPError(
                400, reason="JSON オブジェクトを指定してください"
            ) from None

    def write_error(self, status_code, **kwargs):
        error = kwargs.get("exc_info", (None, None, None))[1]
        self.finish({"error": getattr(error, "reason", None) or "操作できませんでした"})

    def render_page(self, template, **context):
        environment = self.settings["jinja_environment"]
        user = self.current_user
        versions = self.settings["asset_versions"]
        values = {
            "base_url": "/hub/",
            "prefix": "/",
            "user": user,
            "static_url": lambda path, **kwargs: "/hub/static/" + path,
            "login_url": "/hub/login",
            "logout_url": "/hub/logout",
            "logo_url": "/hub/home",
            "xsrf_token": self.xsrf_token.decode(),
            "parsed_scopes": {"admin-ui"} if user.admin else set(),
            "admin_access": user.admin,
            "services": [],
            "no_spawner_check": True,
            "hpc_static_versions": versions,
            "hpc_external_api_enabled": True,
            "hpc_ssh_access_enabled": True,
            "hpc_public_scheme": "http",
            "hpc_job_dns_domain": "localhost",
            "hpc_portal_admin_users": ["admin"],
            "hpc_litellm_public_base_url": "https://llm.example.com/v1",
            "hpc_litellm_admin_url": "",
            "hpc_jupyter_ubuntu_version": "24.04",
            "hpc_openwebui_version": "0.8.0",
            "hpc_ollama_version": "0.18.0",
            "hpc_resource_snapshot": self.state.resource_snapshot(),
            "hpc_memory_overuse": self.state.memory_usage(user),
            "hpc_shared_ollama_detail": self.state.ollama_detail(),
        }
        values["announcement"] = environment.get_template(
            "preview/controls.html"
        ).render(
            xsrf_token=values["xsrf_token"],
            username=user.name,
            users=self.state.accounts.values(),
            scenarios=SCENARIOS,
            scenario=self.state.scenario,
            next_url=self.request.path,
        )
        values["xsrf"] = values["xsrf_token"]
        values["authenticator"] = SimpleNamespace(request_otp=False)
        values.update(context)
        self.set_header("Content-Type", "text/html; charset=UTF-8")
        self.finish(environment.get_template(template).render(values))


class PageHandler(PreviewHandler):
    def initialize(self, page):
        self.page = page

    def get(self, name=None, server_name=None):
        user = self.current_user
        if self.page == "admin":
            self.require_admin()
            self.redirect("/hub/admin/users")
        elif self.page == "admin_users":
            self.require_admin()
            self.render_page(
                "admin_users.html",
                users=list(self.state.accounts.values()),
                grant_sudo_default=False,
            )
        elif self.page == "home":
            self.render_page("home.html")
        elif self.page == "external_api":
            self.redirect("/hub/api-publications#api-tokens")
        elif self.page == "publications":
            enabled = self.state.accounts[user.name]["external_api_enabled"]
            self.render_page(
                "api_publications.html",
                configured=True,
                api_available=enabled
                and self.state.credentials[user.name].get("service_state") == "ready",
                credential_enabled=enabled,
                service_issued=self.state.credentials[user.name].get("service_state")
                == "ready",
                hub_ready=self.state.credentials[user.name].get("hub_state") == "ready",
                credential_state=(
                    "Service Token発行済み"
                    if self.state.credentials[user.name].get("service_state") == "ready"
                    else "Service Token未発行"
                )
                if enabled
                else "利用停止中",
                public_url_prefix=(
                    f"https://portal.example.com/hub/user-api/{user.name}/"
                ),
            )
        elif self.page == "ssh":
            self.render_page("ssh_access.html", configured=True)
        elif self.page == "password":
            self.render_page("account_password.html")
        elif self.page == "llm_api":
            self.render_page(
                "llm_api.html",
                api_disabled=self.state.accounts[user.name]["api_access"] == "disabled",
                status_error="",
                models=[{"id": model} for model in self.state.models],
                models_error="モックのモデル取得エラー"
                if self.state.scenario == "error"
                else "",
                default_model=self.state.models[0] if self.state.models else "",
            )
        elif self.page == "new":
            if name and name != user.name:
                raise web.HTTPError(403)
            spawner = SimpleNamespace(user=user)
            form = render_options_form(
                spawner,
                resource=self.state.resource_snapshot(),
                portal_admin=user.admin,
                shared=self.state.ollama_detail(),
                recommendations=self.state.recommendations(),
                shared_ollama_gpu_label="1 GPU",
                settings=FORM_SETTINGS,
                static_versions=self.settings["asset_versions"],
            )
            self.render_page(
                "spawn.html",
                spawner_options_form=self.xsrf_form_html() + form,
                url=self.request.path,
                for_user=user,
                error_message=self.get_query_argument("error", ""),
            )
        elif self.page == "app":
            if name == "shared-ollama":
                self.require_admin()
                detail = self.state.ollama_detail()
            elif name in user.spawners and user.spawners[name].active:
                detail = self.state.app_detail(user, name)
            else:
                self.redirect("/hub/home")
                return
            self.render_page(
                "app_detail.html",
                detail=detail,
                node_resources=self.state.resource_snapshot(),
            )
        elif self.page == "login":
            self.render_page(
                "login.html",
                user=None,
                username="alice",
                login_error="",
                login_service="",
                authenticator_login_url="/hub/login",
                next="/hub/home",
            )
        elif self.page == "logout":
            self.clear_cookie("preview_user")
            self.redirect("/hub/login")
        elif self.page == "token":
            self.render_page(
                "token.html",
                api_tokens=list(self.state.tokens[user.name].values()),
                oauth_clients=[],
                token_expires_in_options_html='<option value="0">Never</option>',
            )
        elif self.page == "jump":
            self.render_page("preview/app.html")

    def post(self, name=None, server_name=None):
        if self.page == "login":
            username = self.get_body_argument("username", "alice")
            if username not in self.state.users:
                raise web.HTTPError(
                    400, reason="モックに登録されたユーザーを選択してください"
                )
            self.set_cookie("preview_user", username, httponly=True, samesite="Lax")
            self.redirect("/hub/home")
            return
        if self.page != "new":
            raise web.HTTPError(405)
        if name and name != self.current_user.name:
            raise web.HTTPError(403)
        form = {
            key: self.get_body_argument(key)
            for key in self.request.body_arguments
            if key != "_xsrf"
        }
        try:
            self.state.start_app(
                self.current_user, server_name or "app-" + self.state.next_id(), form
            )
        except ValueError as exc:
            self.redirect("/hub/new?" + urlencode({"error": str(exc)}))
            return
        self.redirect("/hub/home")


class ControlsHandler(PreviewHandler):
    def post(self):
        username = self.get_body_argument("username", self.current_user.name)
        scenario = self.get_body_argument("scenario", self.state.scenario)
        if scenario not in SCENARIOS or username not in self.state.users:
            raise web.HTTPError(400)
        if scenario != self.state.scenario or self.get_body_argument("reset", ""):
            self.state.reset(scenario)
        if username not in self.state.users:
            username = "alice"
        self.set_cookie("preview_user", username, httponly=True, samesite="Lax")
        path = self.get_body_argument("next", "/hub/home")
        if (
            not path.startswith("/hub/")
            or any(char in path for char in "\\\r\n?#")
            or "/preview/" in path
        ):
            path = "/hub/home"
        if username != "admin" and ("/admin" in path or "shared-ollama" in path):
            path = "/hub/home"
        self.redirect(path)


class MockApiHandler(PreviewHandler):
    def initialize(self, kind):
        self.kind = kind

    def prepare(self):
        if self.kind.startswith("admin"):
            self.require_admin()
        if self.state.scenario == "error" and self.kind not in {
            "user",
            "hub_token",
            "server",
        }:
            raise web.HTTPError(
                503,
                reason="モックの接続エラーです。表示パターンを切り替えると復旧します。",
            )

    def get(self, username=None, token_id=None):
        user = self.current_user
        if self.kind == "resources":
            self.finish(self.state.resource_snapshot())
        elif self.kind == "memory":
            self.finish(
                {"apps": self.state.memory_usage(user), "updated_at": time.time()}
            )
        elif self.kind == "user":
            if username and username != user.name:
                raise web.HTTPError(403)
            self.finish(
                {
                    "name": user.name,
                    "servers": {
                        name: {
                            "ready": not bool(spawner.pending),
                            "pending": spawner.pending,
                            "url": spawner.public_url,
                        }
                        for name, spawner in user.spawners.items()
                        if spawner.active
                    },
                }
            )
        elif self.kind == "version":
            self.finish(
                {
                    "ok": True,
                    "data": {
                        "running_version": "0.8.0",
                        "target_version": "0.8.0",
                        "verified": True,
                        "update_available": False,
                    },
                }
            )
        elif self.kind == "ports":
            self.require_external_api()
            self.finish(self.state.ports())
        elif self.kind == "publications":
            enabled = self.state.accounts[user.name]["external_api_enabled"]
            self.finish(
                {
                    "apps": [
                        row if enabled else {**row, "state": "disabled"}
                        for row in self.state.publications[user.name].values()
                    ]
                }
            )
        elif self.kind == "admin_apps":
            self.finish(self.state.admin_apps())
        elif self.kind == "admin_users":
            self.finish({"users": list(self.state.accounts.values())})
        else:
            raise web.HTTPError(405)

    def post(self, username=None, token_id=None):
        try:
            self.finish(self.operate(self.json_body(), username))
        except ValidationError:
            raise web.HTTPError(400, reason="入力内容を確認してください") from None
        except (ValueError, KeyError) as exc:
            raise web.HTTPError(400, reason=str(exc)) from None

    def operate(self, data, username):
        user = self.current_user
        if self.kind == "credentials":
            self.require_external_api()
            action = Operation.model_validate(data).action
            if action not in {
                "issue",
                "revoke_cloudflare",
                "revoke_jupyterhub",
                "reveal",
                "download",
                "rotate_cloudflare",
                "rotate_jupyterhub",
            }:
                raise ValueError("操作が不正です")
            if action == "download":
                self.set_header(
                    "Content-Disposition", 'attachment; filename="hpc-api.json"'
                )
            return self.state.credential_payload(user.name, action)
        if self.kind == "ssh_credentials":
            return self.state.ssh_payload(user.name, data.get("action"))
        if self.kind == "publications":
            self.require_external_api()
            if "action" not in data:
                request = Registration.model_validate(data)
                app = self.state.register_publication(user.name, request.model_dump())
            else:
                request = Operation.model_validate(data)
                records = self.state.publications[user.name]
                app = records[request.name]
                if request.action == "delete":
                    del records[request.name]
                elif request.action in {"publish", "unpublish"}:
                    app["state"] = (
                        "published" if request.action == "publish" else "unpublished"
                    )
                else:
                    raise ValueError("操作が不正です")
            return {"ok": True, "app": app}
        if self.kind == "llm":
            if (
                data.get("action") != "regenerate"
                or self.state.accounts[user.name]["api_access"] == "disabled"
            ):
                raise ValueError("API keyを再発行できません")
            return {
                "ok": True,
                "api_key": "mock-llm-key-" + self.state.next_id(),
                "api_base_url": "https://llm.example.com/v1",
            }
        if self.kind == "password":
            request = HpcPasswordChangeRequest.model_validate(data)
            if (
                not request.current_password
                or len(request.new_password) < 8
                or request.new_password != request.confirm_password
            ):
                raise ValueError(
                    "現在のパスワードと、8文字以上の一致する新しいパスワードを入力してください"
                )
            return {"ok": True, "message": "パスワードを変更しました（モック）"}
        if self.kind == "admin_users":
            request = HpcAdminUsersRequest.model_validate(data).model_dump()
            if request["action"].startswith("ollama_"):
                return self.state.ollama_action(request)
            if request["action"] == "create":
                self.set_status(201)
            return self.state.account_action(user.name, request)
        if self.kind == "hub_token":
            if username != user.name:
                raise web.HTTPError(403)
            return self.state.issue_hub_token(user.name, data)
        raise web.HTTPError(405)

    def delete(self, username, token_id=None):
        if unquote(username) != self.current_user.name:
            raise web.HTTPError(403)
        if self.kind == "server":
            self.current_user.spawners.pop(token_id or "", None)
        elif self.kind == "hub_token":
            self.state.tokens[username].pop(token_id, None)
        else:
            raise web.HTTPError(405)
        self.set_status(204)
        self.finish()
