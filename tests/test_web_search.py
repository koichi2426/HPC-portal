"""SearXNGとOpen WebUI Web検索の安全な初期構成を検証する。"""

import json
from pathlib import Path

from hpc_portal import batch, settings


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_web_search_settings_use_local_searxng_with_bounded_workload():
    assert settings.HPC_SEARXNG_QUERY_URL == (
        "http://127.0.0.1:8888/search?q=<query>"
    )
    assert settings.OPENWEBUI_WEB_SEARCH_RESULT_COUNT == 6
    assert settings.OPENWEBUI_WEB_SEARCH_CONCURRENT_REQUESTS == 1
    assert settings.OPENWEBUI_WEB_LOADER_CONCURRENT_REQUESTS == 2
    assert settings.OPENWEBUI_WEB_FETCH_MAX_CONTENT_LENGTH == 12000


def test_openwebui_enables_selected_builtin_tools_by_default():
    metadata = json.loads(batch.OPENWEBUI_DEFAULT_MODEL_METADATA_JSON)
    params = json.loads(batch.OPENWEBUI_DEFAULT_MODEL_PARAMS_JSON)
    script = batch.c.HPCSlurmSpawner.batch_script

    assert metadata["capabilities"]["web_search"] is True
    assert metadata["capabilities"]["code_interpreter"] is True
    assert metadata["capabilities"]["builtin_tools"] is True
    assert metadata["builtinTools"]["time"] is True
    assert metadata["builtinTools"]["memory"] is True
    assert metadata["builtinTools"]["notes"] is True
    assert metadata["builtinTools"]["web_search"] is True
    assert metadata["builtinTools"]["code_interpreter"] is True
    assert metadata["defaultFeatureIds"] == ["web_search", "code_interpreter"]
    assert params["think"] is False
    assert params["reasoning_effort"] == "none"
    assert params["function_calling"] == "native"
    assert '"ENABLE_WEB_SEARCH=True"' in script
    assert '"ENABLE_CODE_INTERPRETER=True"' in script
    assert '"WEB_SEARCH_ENGINE=searxng"' in script
    assert '"SEARXNG_QUERY_URL=http://127.0.0.1:8888/search?q=<query>"' in script


def test_openwebui_keeps_private_url_fetch_ssrf_protection_enabled():
    script = batch.c.HPCSlurmSpawner.batch_script

    assert '"ENABLE_RAG_LOCAL_WEB_FETCH=False"' in script
    assert '"BYPASS_WEB_SEARCH_WEB_LOADER=False"' in script


def test_searxng_service_listens_on_loopback_and_has_no_cloudflare_route():
    service = (
        REPOSITORY_ROOT / "roles/searxng/templates/searxng.service.j2"
    ).read_text()
    cloudflared_vars = (REPOSITORY_ROOT / "group_vars/all/main.yml").read_text()

    assert "GRANIAN_HOST={{ searxng_host }}" in service
    assert "--pwd /usr/local/searxng" in service
    assert 'searxng_host: "127.0.0.1"' in cloudflared_vars
    assert "hostname: \"{{ searxng" not in cloudflared_vars


def test_searxng_limits_engines_and_enables_json_without_limiter():
    config = (
        REPOSITORY_ROOT / "roles/searxng/templates/settings.yml.j2"
    ).read_text()

    assert "    - json" in config
    assert "    keep_only:" in config
    assert "      - duckduckgo" in config
    assert "      - brave" in config
    assert "      - wikipedia" in config
    assert "  limiter: false" in config
    assert "  image_proxy: false" in config
    assert "secret_key: {{ searxng_secret_key | to_json }}" in config
    assert "REPLACE_WITH_RANDOM_SEARXNG_SECRET_KEY" not in config
