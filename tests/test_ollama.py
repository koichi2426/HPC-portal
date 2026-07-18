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
