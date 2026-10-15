"""共有Ollama管理の検証、コマンド構築、進捗変換を検証する。"""

from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "model", ["../bad model", "model;id", "$(id)", "a" * 129, "日本語"]
)
def test_ollama_command_rejects_injectable_model_without_execution(monkeypatch, model):
    monkeypatch.setattr(
        ollama.commands, "run", lambda command: pytest.fail("実行されてはいけません")
    )

    data, error = ollama.command("pull", model)

    assert data is None
    assert "使用できない文字" in error


@pytest.fixture(autouse=True)
def usecase_components(portal_dependencies):
    global ollama, portal
    portal = portal_dependencies
    ollama = portal_dependencies.ollama.backend
