"""共有Ollama管理の検証、コマンド構築、進捗変換を検証する。"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hpc_portal import ollama


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_ollama_detail_script_uses_handler_context_across_jinja_blocks():
    """scriptブロックがmainブロック内のローカル変数へ依存しないことを確認する。"""
    template = (
        REPOSITORY_ROOT / "roles/jupyterhub/templates/app_detail.html.j2"
    ).read_text()
    script_block = template.split("{% block script %}", 1)[1]

    assert "{% if detail.shared_ollama %}" in script_block
    assert "d.shared_ollama" not in script_block


def test_ollama_detail_skips_model_api_while_api_is_starting(monkeypatch):
    """API待機中はモデル一覧を取得せず、接続拒否を警告にしないことを確認する。"""
    actions = []

    def fake_command(action):
        actions.append(action)
        return ({"running": True, "api": False, "job_ids": "42"}, None)

    monkeypatch.setattr(ollama, "_hpc_ollama_cmd", fake_command)

    detail = ollama._hpc_shared_ollama_detail_context()

    assert actions == ["status"]
    assert detail["active"] is True
    assert detail["api"] is False
    assert detail["models"] == []
    assert detail["status_error"] == ""


@pytest.mark.parametrize(
    ("cpus", "memory", "expected"),
    [(None, None, ("8", "64G", None)), ("8", "32g", ("8", "32G", None))],
)
def test_ollama_resources_accept_allowed_values(cpus, memory, expected):
    assert ollama._hpc_validate_ollama_resources(cpus, memory) == expected


@pytest.mark.parametrize(("cpus", "memory"), [("0", "16G"), ("4", "1G"), ("20", "999G")])
def test_ollama_resources_reject_values_outside_allowlist(cpus, memory):
    normalized_cpu, normalized_memory, error = ollama._hpc_validate_ollama_resources(cpus, memory)

    assert normalized_cpu is None
    assert normalized_memory is None
    assert error


def test_ollama_start_builds_fixed_command_arguments(monkeypatch):
    commands = []
    monkeypatch.setattr(
        ollama,
        "_hpc_run_cmd",
        lambda command: commands.append(command)
        or SimpleNamespace(returncode=0, stdout='{"running":true}', stderr=""),
    )

    data, error = ollama._hpc_ollama_cmd("start", cpus="8", memory="32G")

    assert error is None
    assert commands == [[
        "/usr/local/sbin/hpc-ollama",
        "start",
        "--cpus",
        "8",
        "--memory",
        "32G",
        "--parallel",
        "2",
        "--max-loaded-models",
        "2",
        "--context-length",
        "131072",
        "--kv-cache-type",
        "q8_0",
        "--keep-alive",
        "30m",
        "--max-queue",
        "64",
        "--flash-attention",
        "1",
    ]]
    assert data["gpus"] == "1"


def test_ollama_start_rejects_runtime_values_outside_allowlist(monkeypatch):
    """許可されていない起動設定を管理コマンドへ渡さないことを確認する。"""
    monkeypatch.setattr(
        ollama,
        "_hpc_run_cmd",
        lambda command: pytest.fail("実行されてはいけません"),
    )

    data, error = ollama._hpc_ollama_cmd("start", parallel="999")

    assert data is None
    assert "同時処理数" in error


@pytest.mark.parametrize("model", ["../bad model", "model;id", "$(id)", "a" * 129, "日本語"])
def test_ollama_command_rejects_injectable_model_without_execution(monkeypatch, model):
    monkeypatch.setattr(ollama, "_hpc_run_cmd", lambda command: pytest.fail("実行されてはいけません"))

    data, error = ollama._hpc_ollama_cmd("pull", model)

    assert data is None
    assert "使用できない文字" in error


def test_ollama_status_preserves_non_json_output_as_raw(monkeypatch):
    monkeypatch.setattr(
        ollama,
        "_hpc_run_cmd",
        lambda command: SimpleNamespace(returncode=0, stdout="not-json", stderr=""),
    )

    assert ollama._hpc_ollama_cmd("status") == ({"raw": "not-json"}, None)


def test_ollama_update_builds_fixed_command_and_parses_json(monkeypatch):
    """Web更新がクライアント入力の版を受け取らず管理コマンドだけを実行する。"""
    commands = []
    monkeypatch.setattr(
        ollama,
        "_hpc_run_cmd",
        lambda command: commands.append(command)
        or SimpleNamespace(
            returncode=0,
            stdout='{"status":"started","job_ids":"43"}',
            stderr="",
        ),
    )

    data, error = ollama._hpc_ollama_cmd("update")

    assert error is None
    assert data == {"status": "started", "job_ids": "43"}
    assert commands == [["/usr/local/sbin/hpc-ollama", "update"]]


def test_ollama_update_check_builds_fixed_command_and_parses_json(monkeypatch):
    commands = []
    monkeypatch.setattr(
        ollama,
        "_hpc_run_cmd",
        lambda command: commands.append(command)
        or SimpleNamespace(
            returncode=0,
            stdout='{"latest_version":"0.33.0","update_available":true}',
            stderr="",
        ),
    )

    data, error = ollama._hpc_ollama_cmd("update-check")

    assert error is None
    assert data["latest_version"] == "0.33.0"
    assert commands == [["/usr/local/sbin/hpc-ollama", "update-check"]]


def test_ollama_update_script_downloads_before_stopping_and_rolls_back():
    """新イメージを先に検証し、起動失敗時に旧イメージへ戻す。"""
    script = (REPOSITORY_ROOT / "roles/ollama/templates/hpc-ollama.j2").read_text()
    worker_block = script.split("  _update-worker)", 1)[1].split("  start)", 1)[0]

    assert worker_block.index('docker://ollama/ollama:${target}') < worker_block.index("scancel $ids")
    assert "apptainer build --disable-cache" in worker_block
    assert worker_block.index('verified_version="$(image_version') < worker_block.index("scancel $ids")
    assert worker_block.index('sudo -u "$SERVICE_USER" test -r "$new_image"') < worker_block.index("scancel $ids")
    assert 'chmod 0644 "$new_image"' in worker_block
    assert 'mv -Tf "$rollback_link" "$OLLAMA_IMAGE"' in worker_block
    assert "rolled_back" in worker_block
    for option in (
        "--cpus",
        "--memory",
        "--parallel",
        "--max-loaded-models",
        "--context-length",
        "--kv-cache-type",
        "--keep-alive",
        "--max-queue",
        "--flash-attention",
    ):
        assert option in worker_block


def test_ollama_update_keeps_only_current_and_previous_images():
    script = (REPOSITORY_ROOT / "roles/ollama/templates/hpc-ollama.j2").read_text()
    worker_block = script.split("  _update-worker)", 1)[1].split("  start)", 1)[0]

    assert '"$image" != "$new_image"' in worker_block
    assert '"$image" != "$old_image"' in worker_block
    assert 'rm -f -- "$image"' in worker_block


def test_ollama_update_state_umask_does_not_leak_to_image_build():
    script = (REPOSITORY_ROOT / "roles/ollama/templates/hpc-ollama.j2").read_text()
    state_block = script.split("write_update_state()", 1)[1].split(
        "update_state_value()", 1
    )[0]

    assert "(\n    umask 077" in state_block
    assert ")\n  mv -f" in state_block


def test_ollama_detail_renders_latest_check_and_progress():
    """共有Ollama詳細に最新版確認、更新、段階表示がある。"""
    template = (
        REPOSITORY_ROOT / "roles/jupyterhub/templates/app_detail.html.j2"
    ).read_text()

    assert "data-hpc-ollama-check-button" in template
    assert "data-hpc-ollama-update-button" in template
    assert "data-hpc-ollama-update-status" in template
    assert "d.latest_version" in template
    admin_js = (
        REPOSITORY_ROOT / "roles/jupyterhub/files/hpc-portal-js/ollama-admin.js"
    ).read_text()
    assert 'hpcOllamaPost({action: "ollama_update_check"})' in admin_js
    assert 'hpcOllamaPost({action: "ollama_update"})' in (
        REPOSITORY_ROOT / "roles/jupyterhub/files/hpc-portal-js/ollama-admin.js"
    ).read_text()


def test_ollama_apptainer_uses_bootstrap_only_for_initial_selection():
    tasks = (REPOSITORY_ROOT / "roles/apptainer/tasks/main.yml").read_text()

    assert "初回のみOllama基準イメージを選択" in tasks
    assert "when: not selected_ollama_image.stat.exists" in tasks
    assert "ollama-{{ ollama_version }}.sif" in tasks


@pytest.mark.parametrize(
    ("payload", "model", "state"),
    [
        ({"active": False}, None, "idle"),
        ({"active": True, "active_model": "qwen:4b"}, "qwen:4b", "pulling"),
        ({"active": True, "active_model": "qwen:4b"}, "other:1b", "busy"),
        ({"active": False, "result": "cancelled"}, None, "cancelled"),
        ({"active": False, "result": "cancelled_cleanup_failed"}, None, "cancelled_cleanup_failed"),
        ({"active": False, "result": "1", "last": "failure"}, None, "failed"),
        ({"active": False, "result": "0", "last": json.dumps({"status": "success"})}, None, "completed"),
    ],
)
def test_pull_progress_maps_backend_state(monkeypatch, payload, model, state):
    monkeypatch.setattr(ollama, "_hpc_ollama_cmd", lambda action, selected=None: (payload, None))

    progress, error = ollama._hpc_ollama_pull_progress(model)

    assert error is None
    assert progress["state"] == state


def test_pull_progress_clamps_numbers_and_error_length(monkeypatch):
    payload = {
        "active": True,
        "last": json.dumps({"completed": -5, "total": "bad", "error": "x" * 500}),
    }
    monkeypatch.setattr(ollama, "_hpc_ollama_cmd", lambda action, model=None: (payload, None))

    progress, _ = ollama._hpc_ollama_pull_progress()

    assert progress["completed"] == 0
    assert progress["total"] is None
    assert len(progress["error"]) == 300


def test_has_model_matches_exact_name(monkeypatch):
    monkeypatch.setattr(
        ollama,
        "_hpc_ollama_cmd",
        lambda action: ({"models": [{"name": "qwen:4b"}, {"name": "other"}]}, None),
    )

    assert ollama._hpc_ollama_has_model("qwen:4b") == (True, None)
    found, error = ollama._hpc_ollama_has_model("qwen:8b")
    assert found is False
    assert "ありません" in error


@pytest.mark.parametrize(
    ("capabilities", "expected"),
    [(["completion", "tools"], True), (["completion"], False)],
)
def test_model_supports_tools_uses_show_capabilities(
    monkeypatch, capabilities, expected
):
    monkeypatch.setattr(
        ollama,
        "_hpc_ollama_cmd",
        lambda action, model: ({"capabilities": capabilities}, None),
    )

    assert ollama._hpc_ollama_model_supports_tools("qwen:4b") == (expected, None)


def test_ollama_show_builds_model_command_and_parses_json(monkeypatch):
    commands = []
    monkeypatch.setattr(
        ollama,
        "_hpc_run_cmd",
        lambda command: commands.append(command)
        or SimpleNamespace(
            returncode=0,
            stdout='{"capabilities":["tools"]}',
            stderr="",
        ),
    )

    data, error = ollama._hpc_ollama_cmd("show", "qwen:4b")

    assert error is None
    assert data == {"capabilities": ["tools"]}
    assert commands == [["/usr/local/sbin/hpc-ollama", "show", "qwen:4b"]]
