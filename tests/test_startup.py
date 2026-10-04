"""配布済みパッケージとJupyterHub起動設定の接続を検証する。"""

import os
import runpy
import subprocess
import sys
from pathlib import Path

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, PrefixLoader
from jupyterhub.app import JupyterHub
from tornado.web import RequestHandler
from traitlets.config import Config

from hpc_portal.entrypoints import jupyterhub as entrypoint
from hpc_portal.entrypoints.handler_registry import register_handlers
from hpc_portal.infrastructure.jupyterhub.slurm_spawner import HPCSlurmSpawner

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


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


def test_handler_registration_preserves_custom_routes_without_duplicates():
    config = Config()
    config.JupyterHub.extra_handlers.append(("/custom", RequestHandler))

    register_handlers(config)
    register_handlers(config)

    routes = [entry[0] for entry in config.JupyterHub.extra_handlers]
    assert routes.count("/custom") == 1
    assert routes.count("/external-api") == 1
    assert routes.count("/admin/users") == 1


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


def test_importing_portal_modules_does_not_register_framework_hooks():
    """import順に依存せず、登録処理は起動エントリポイントだけが行う。"""
    env = dict(os.environ, PYTHONPATH=str(REPOSITORY_ROOT / "src"))
    code = """
import importlib
import pkgutil
from jupyterhub.handlers.base import BaseHandler
from jupyterhub.handlers.login import LoginHandler
import jupyterhub.handlers
clear_cookie = BaseHandler.clear_login_cookie
login_get = LoginHandler.get
handlers = list(jupyterhub.handlers.default_handlers)
package = importlib.import_module('hpc_portal')
for module in pkgutil.walk_packages(package.__path__, package.__name__ + '.'):
    importlib.import_module(module.name)
assert BaseHandler.clear_login_cookie is clear_cookie
assert LoginHandler.get is login_get
assert jupyterhub.handlers.default_handlers == handlers
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


def test_frontend_templates_inherit_hub_templates_and_render_api_page():
    """AnsibleでそのままコピーするHTMLをHubのJinja環境で描画できる。"""
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
    )
    for path in templates.glob("*.html"):
        environment.get_template(path.name)

    rendered = environment.get_template("external_api.html").render(
        state="ready",
        configured=True,
        base_url="/hub/",
        static_url=lambda path, **kwargs: "/hub/static/" + path,
        hpc_static_versions={"js/core.js": "1", "js/external-api.js": "1"},
        hpc_external_api_enabled=True,
    )

    assert "API トークン・接続設定" in rendered
    assert 'href="/hub/api-publications"' in rendered
    assert "{% extends" not in rendered
