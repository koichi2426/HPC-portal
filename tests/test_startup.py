"""配布済みパッケージとJupyterHub起動設定の接続を検証する。"""

import os
import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import ChoiceLoader, Environment, FileSystemLoader, PrefixLoader
from jupyterhub.app import JupyterHub
from tornado import web
from tornado.httputil import HTTPServerRequest
from traitlets.config import Config

from hpc_portal.bootstrap import container
from hpc_portal.entrypoints import jupyterhub as entrypoint
from hpc_portal.infrastructure.jupyterhub.slurm_spawner import HPCSlurmSpawner
from hpc_portal.presentation.handlers import external_api

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_account_api_dependencies_belong_to_their_container(monkeypatch):
    """個別に組み立てたcontainerが、グローバルな共有先へ混ざらない。"""
    monkeypatch.setenv("HPC_EXTERNAL_API_ENABLED", "true")
    first = container.build_container()
    second = container.build_container()
    first.external_api = SimpleNamespace(name="first")
    second.external_api = SimpleNamespace(name="second")
    monkeypatch.setattr(container, "_container", second)

    assert (
        first.users.provision_external_api.external_api_factory() is first.external_api
    )
    assert second.users.snapshot.external_api_factory() is second.external_api
    assert container.get_external_api() is second.external_api
    assert first.openwebui_key_locks is not second.openwebui_key_locks


def test_disabled_api_does_not_initialize_the_container(monkeypatch):
    """外部API無効時は、専用設定の検証やクライアント生成を行わない。"""
    monkeypatch.setenv("HPC_EXTERNAL_API_ENABLED", "false")
    monkeypatch.setattr(container, "_container", None)

    def unexpected_initialization():
        raise AssertionError("無効な外部APIがcontainerを初期化しました")

    monkeypatch.setattr(container, "build_container", unexpected_initialization)
    assert container.get_external_api() is None


def test_deployment_entrypoint_loads_hub_and_spawner(portal_dependencies, monkeypatch):
    """実際のConfig複製後もサービス・排他制御を共有できる。"""
    monkeypatch.setenv("HPC_EXTERNAL_API_ENABLED", "false")
    config = Config()
    runpy.run_path(
        str(REPOSITORY_ROOT / "ansible/roles/jupyterhub/files/jupyterhub_config.py"),
        init_globals={"get_config": lambda: config},
    )

    hub = JupyterHub(config=config)
    first = HPCSlurmSpawner(config=config)
    second = HPCSlurmSpawner(config=config)

    assert first.portal_dependencies is portal_dependencies
    assert second.portal_dependencies is first.portal_dependencies
    assert hub.spawner_class is HPCSlurmSpawner
    assert hub.cleanup_servers is False
    assert "#SBATCH" in first.batch_script
    assert callable(hub.template_vars["hpc_shared_ollama_detail"])
    assert hub.template_vars["hpc_external_api_enabled"] is False


def test_enabling_api_adds_dedicated_scope_without_opening_remote_connections(
    monkeypatch,
):
    monkeypatch.setenv("HPC_EXTERNAL_API_ENABLED", "true")
    scheduled = []
    monkeypatch.setattr(entrypoint, "start_background_tasks", scheduled.append)
    config = Config()

    entrypoint.configure_jupyterhub(config)
    hub = JupyterHub(config=config)

    assert "custom:external-api:invoke" in hub.custom_scopes
    assert hub.token_expires_in_max_seconds == 0
    assert scheduled == [True]


def test_usecases_import_without_runtime_configuration():
    """業務手順はHub・OS監視ライブラリや環境設定なしでも読み込める。"""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("HPC_", "LITELLM_", "OPENWEBUI_"))
    }
    env["PYTHONPATH"] = str(REPOSITORY_ROOT / "src")
    code = """
import importlib
import pkgutil
import sys
for name in ('jupyterhub', 'tornado', 'batchspawner', 'psutil', 'aiohttp'):
    sys.modules[name] = None
package = importlib.import_module('hpc_portal.application.usecase')
for module in pkgutil.iter_modules(package.__path__, package.__name__ + '.'):
    importlib.import_module(module.name)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("enabled", [False, True])
async def test_frontend_templates_inherit_hub_templates_and_render_api_page(
    enabled, monkeypatch
):
    """有効・無効のどちらでも、HubのHandler経由でAPI画面を描画できる。"""
    monkeypatch.setenv("HPC_EXTERNAL_API_ENABLED", str(enabled).lower())
    monkeypatch.setattr(
        external_api,
        "get_external_api",
        lambda: SimpleNamespace(
            queries=SimpleNamespace(
                credential_record=lambda user: {"enabled": True, "state": "ready"}
            )
        ),
    )
    templates = REPOSITORY_ROOT / "frontend/templates"
    base_path = JupyterHub()._template_paths_default()[0]
    environment = Environment(
        loader=ChoiceLoader(
            [
                PrefixLoader({"templates": FileSystemLoader(base_path)}, "/"),
                FileSystemLoader([str(templates), base_path]),
            ]
        ),
        autoescape=True,
        enable_async=True,
    )
    for path in templates.glob("*.html"):
        environment.get_template(path.name)

    application = web.Application(
        hub=SimpleNamespace(base_url="/hub/"),
        base_url="/hub/",
        authenticator=SimpleNamespace(login_service="Test"),
        login_url="/hub/login",
        logout_url="/hub/logout",
        jinja2_env=environment,
        template_vars={
            "hpc_static_versions": {
                "portal_css": "1",
                "js/core.js": "1",
                "js/external-api.js": "1",
                "js/api-publications.js": "1",
            },
            "hpc_external_api_enabled": enabled,
        },
    )
    request = HTTPServerRequest(
        method="GET",
        uri="/hub/api-publications",
        connection=SimpleNamespace(set_close_callback=lambda callback: None),
    )
    handler = external_api.ApiPublicationsPage(application, request)
    handler._jupyterhub_user = SimpleNamespace(
        name="alice",
        json_escaped_name="alice",
        spawner=SimpleNamespace(options_form=""),
    )
    handler.parsed_scopes, handler.expanded_scopes = {}, set()
    monkeypatch.setattr(
        external_api.ApiPublicationsPage, "xsrf_token", property(lambda self: b"test")
    )
    monkeypatch.setattr(
        handler, "static_url", lambda path, **kwargs: "/hub/static/" + path
    )
    responses = []
    monkeypatch.setattr(handler, "finish", responses.append)

    # テンプレートの共通変数も本番のBaseHandlerで生成し、属性名の衝突を検知する。
    await handler.get()
    rendered = responses[0]

    assert rendered.strip()
    assert "{% extends" not in rendered
    assert handler.services == {}
    assert ('href="/hub/api-publications"' in rendered) is enabled
    assert ("自作API公開は未設定です。" in rendered) is not enabled

    users = [
        dict(
            username=name,
            uid=1001 + index,
            home=f"/home/{name}",
            display_name="",
            protected=name == "admin",
            sudo_enabled=name == "admin",
            api_access="disabled" if name == "bob" else "enabled",
            api_access_message="",
            storage_used_bytes=0,
            storage_used_label="0 B",
            storage_message="",
            external_api_enabled=name != "bob",
            external_api_state="disabled" if name == "bob" else "ready",
        )
        for index, name in enumerate(["alice", "bob", "admin"])
    ]
    admin_page = await handler.render_template(
        "admin_users.html", users=users, grant_sudo_default=False
    )

    assert ("<th>自作API公開</th>" in admin_page) is enabled
    assert ('data-action="external_api_enable"' in admin_page) is enabled
    assert ('data-action="external_api_disable"' in admin_page) is enabled
    assert "LLM API有効化" in admin_page
    assert "LLM API無効化" in admin_page
    assert ("自作API公開有効化" in admin_page) is enabled
    assert ("自作API公開無効化" in admin_page) is enabled
    assert 'role="switch"' not in admin_page
    assert "外部 API:" not in admin_page
