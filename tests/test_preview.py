"""モック環境の画面描画と、主要操作の状態更新を確認する。"""

import json
import os
import subprocess
import sys
from http.cookies import SimpleCookie
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from tornado.testing import AsyncHTTPTestCase

from dev.preview.mock_data import SCENARIOS, MockState
from dev.preview.server import create_application


class TestPreview(AsyncHTTPTestCase):
    def get_app(self):
        self.state = MockState()
        return create_application(self.state)

    def headers(self, username="alice"):
        response = self.fetch("/hub/home")
        cookies = SimpleCookie()
        for value in response.headers.get_list("Set-Cookie"):
            cookies.load(value)
        token = cookies["_xsrf"].value
        return {
            "Cookie": f"_xsrf={token}; preview_user={username}",
            "X-XSRFToken": token,
            "Content-Type": "application/json",
        }

    def post_json(self, path, payload, username="alice"):
        return self.fetch(
            path,
            method="POST",
            headers=self.headers(username),
            body=json.dumps(payload),
        )

    def test_production_pages_and_assets_render_without_runtime_dependencies(self):
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("HPC_", "LITELLM_", "OPENWEBUI_"))
        }
        env["PYTHONPATH"] = "src"
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                """
import sys
sys.modules['hpc_portal.infrastructure.config.settings'] = None
sys.modules['hpc_portal.bootstrap.container'] = None
from dev.preview.server import create_application
create_application()
""",
            ],
            env=env,
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        pages = [
            "home",
            "new",
            "external-api",
            "api-publications",
            "llm-api",
            "account/password",
            "apps/notebook",
            "apps/chat",
            "token",
            "login",
        ]
        with patch(
            "hpc_portal.presentation.job_form.get_container",
            side_effect=AssertionError("本番の依存を利用しています"),
        ):
            for page in pages:
                response = self.fetch("/hub/" + page)
                assert response.code == 200, (page, response.body)
                assert "モック環境" in response.body.decode()
            for page in ("admin/users", "apps/shared-ollama"):
                response = self.fetch("/hub/" + page, headers=self.headers("admin"))
                assert response.code == 200, (page, response.body)
        for path in (
            "hpc-portal.css",
            "hpc-js/core.js",
            "static/css/style.min.css",
            "static/components/bootstrap/dist/js/bootstrap.bundle.min.js",
            "logo",
        ):
            response = self.fetch("/hub/" + path)
            assert response.code == 200, (path, response.body)
        assert self.fetch("/hub/admin/users").code == 403

    def test_start_refresh_and_stop_keep_consistent_state(self):
        headers = self.headers()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        response = self.fetch(
            "/hub/new",
            method="POST",
            headers=headers,
            body=urlencode(
                {
                    "app_choice": "ubuntu-cli",
                    "cpu": "2",
                    "mem": "4",
                    "hours": "unlimited",
                }
            ),
            follow_redirects=False,
        )
        assert response.code == 302
        names = [
            name
            for name in self.state.users["alice"].spawners
            if name.startswith("app-")
        ]
        assert len(names) == 1
        name = names[0]
        assert (
            self.state.users["alice"].spawners[name].user_options["runtime"]
            == "UNLIMITED"
        )
        assert (
            json.loads(self.fetch("/hub/api/user").body)["servers"][name]["pending"]
            == "spawn"
        )
        self.state.users["alice"].spawners[name].ready_at = 1
        assert (
            json.loads(self.fetch("/hub/api/user").body)["servers"][name]["ready"]
            is True
        )
        assert self.fetch(f"/hub/apps/{name}").code == 200
        response = self.fetch(
            f"/hub/api/users/alice/servers/{name}",
            method="DELETE",
            headers=self.headers(),
        )
        assert response.code == 204
        assert name not in json.loads(self.fetch("/hub/api/user").body)["servers"]
        response = self.fetch(f"/hub/apps/{name}", follow_redirects=False)
        assert response.code == 302

    def test_credentials_rotate_independently_and_publication_changes_persist(self):
        path = "/hub/external-api/credentials"
        original = json.loads(self.post_json(path, {"action": "reveal"}).body)
        rotated = json.loads(self.post_json(path, {"action": "rotate_cloudflare"}).body)
        assert rotated["client_secret"] != original["client_secret"]
        assert rotated["jupyterhub_token"] == original["jupyterhub_token"]
        rotated_hub = json.loads(
            self.post_json(path, {"action": "rotate_jupyterhub"}).body
        )
        assert rotated_hub["jupyterhub_token"] != rotated["jupyterhub_token"]
        assert rotated_hub["client_secret"] == rotated["client_secret"]
        for action, state in (("unpublish", "unpublished"), ("publish", "published")):
            response = self.post_json(
                "/hub/api-publications/api", {"name": "analyze", "action": action}
            )
            assert response.code == 200, response.body
            rows = json.loads(self.fetch("/hub/api-publications/api").body)["apps"]
            assert rows[0]["state"] == state
        response = self.post_json(
            "/hub/api-publications/api",
            {
                "name": "worker",
                "display_name": "計算 API",
                "candidate": "mock-worker",
                "port": 8080,
                "health_path": "/health",
            },
        )
        assert response.code == 200, response.body
        assert (
            len(json.loads(self.fetch("/hub/api-publications/api").body)["apps"]) == 2
        )
        response = self.post_json(
            "/hub/api-publications/api", {"name": "worker", "action": "delete"}
        )
        assert response.code == 200
        assert "worker" not in self.state.publications["alice"]
        response = self.post_json(path, {"action": "download"})
        assert "attachment" in response.headers["Content-Disposition"]

    def test_admin_operations_modify_only_mock_records(self):
        path = "/hub/admin/users/api"
        assert (
            self.post_json(path, {"action": "create", "username": "charlie"}).code
            == 403
        )
        response = self.post_json(
            path,
            {"action": "create", "username": "charlie", "display_name": "確認用"},
            "admin",
        )
        assert response.code == 201, response.body
        response = self.post_json(
            path,
            {"action": "display_name", "username": "charlie", "display_name": "更新後"},
            "admin",
        )
        assert response.code == 200
        assert self.state.accounts["charlie"]["display_name"] == "更新後"
        assert (
            self.post_json(
                path, {"action": "external_api_disable", "username": "alice"}, "admin"
            ).code
            == 200
        )
        assert (
            self.post_json("/hub/external-api/credentials", {"action": "reveal"}).code
            == 403
        )
        assert self.post_json(path, {"action": "ollama_stop"}, "admin").code == 200
        assert not self.state.ollama["running"]
        assert (
            self.post_json(
                path, {"action": "delete", "username": "charlie"}, "admin"
            ).code
            == 200
        )
        assert "charlie" not in self.state.accounts

    def test_scenarios_render_and_browser_control_resets_data(self):
        for scenario in SCENARIOS:
            self.state.reset(scenario)
            for page in ("home", "new", "llm-api", "external-api", "api-publications"):
                response = self.fetch("/hub/" + page)
                assert response.code == 200, (scenario, page, response.body)
        headers = self.headers()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        response = self.fetch(
            "/hub/preview/settings",
            method="POST",
            headers=headers,
            body=urlencode(
                {"username": "admin", "scenario": "empty", "next": "/hub/home"}
            ),
            follow_redirects=False,
        )
        assert response.code == 302
        assert self.state.scenario == "empty"
        assert not self.state.users["alice"].spawners
        assert any(
            "preview_user=admin" in value
            for value in response.headers.get_list("Set-Cookie")
        )
        self.state.reset("error")
        assert self.fetch("/hub/api-publications/ports").code == 503
        assert self.fetch("/hub/home").code == 200
        response = self.fetch(
            "/hub/preview/settings", method="POST", body="", follow_redirects=False
        )
        assert response.code == 403
