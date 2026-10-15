"""OllamaとLiteLLM間のモデル同期仕様を検証する。"""

from unittest.mock import ANY

import pytest

from hpc_portal.infrastructure.ollama import ollama_client as ollama


def _deployment(
    backend="ollama_chat/qwen:4b",
    model_id="db-1",
    *,
    source="hpc-portal-ollama",
    db_model=True,
):
    """LiteLLM deploymentのテストデータを作成する。"""
    return {
        "model_name": "qwen:4b",
        "litellm_params": {"model": backend},
        "model_info": {
            "id": model_id,
            "db_model": db_model,
            "source": source,
        },
    }


def _mock_ollama_model(monkeypatch, *, supports_tools=True):
    """登録テスト用のOllama応答を設定する。"""
    monkeypatch.setattr(ollama, "has_model", lambda model: (True, None))
    monkeypatch.setattr(
        ollama,
        "model_supports_tools",
        lambda model: (supports_tools, None),
    )


def test_register_model_verifies_chat_backend_before_deleting_legacy(monkeypatch):
    calls = []
    legacy = _deployment("ollama/qwen:4b", "legacy-1")
    correct = _deployment("ollama_chat/qwen:4b", "chat-1")
    responses = iter([({"data": [legacy]}, None), ({"data": [legacy, correct]}, None)])
    monkeypatch.setattr(models.client, "enabled", lambda: True)
    _mock_ollama_model(monkeypatch)
    monkeypatch.setattr(models.gateway, "model_info", lambda: next(responses))
    monkeypatch.setattr(
        models.client,
        "request",
        lambda path, payload: calls.append((path, payload)) or {"ok": True},
    )

    result, error = models.register_ollama_model.execute("qwen:4b")

    assert error is None
    assert result["migrated"] == 1
    assert calls == [
        ("/model/new", ANY),
        ("/model/delete", {"id": "legacy-1"}),
    ]


def test_register_model_preserves_legacy_when_chat_backend_verification_fails(
    monkeypatch,
):
    calls = []
    legacy = _deployment("ollama/qwen:4b", "legacy-1")
    responses = iter([({"data": [legacy]}, None), ({"data": [legacy]}, None)])
    monkeypatch.setattr(models.client, "enabled", lambda: True)
    _mock_ollama_model(monkeypatch)
    monkeypatch.setattr(models.gateway, "model_info", lambda: next(responses))
    monkeypatch.setattr(
        models.client,
        "request",
        lambda path, payload: calls.append((path, payload)) or {"ok": True},
    )

    result, error = models.register_ollama_model.execute("qwen:4b")

    assert result is None
    assert "確認できません" in error
    assert [path for path, _payload in calls] == ["/model/new"]


def test_delete_model_deletes_only_matching_db_deployments_once(monkeypatch):
    calls = []
    response = {
        "data": [
            _deployment("ollama/qwen:4b", "db-1"),
            _deployment("ollama/qwen:4b", "db-1"),
            _deployment("ollama_chat/qwen:4b", "db-2"),
            _deployment("ollama_chat/qwen:4b", "manual-1", source="manual"),
            {
                "model_name": "other",
                "litellm_params": {"model": "ollama/other"},
                "model_info": {"id": "db-2", "db_model": True},
            },
        ]
    }

    def fake_request(path, payload=None, method="POST"):
        """一覧取得結果を返し、削除要求を記録する。"""
        calls.append((path, payload, method))
        return response if path == "/v1/model/info" else {"ok": True}

    monkeypatch.setattr(models.client, "enabled", lambda: True)
    monkeypatch.setattr(models.client, "request", fake_request)

    assert models.delete_ollama_model.execute("qwen:4b") is None
    assert calls == [
        ("/v1/model/info", None, "GET"),
        ("/model/delete", {"id": "db-1"}, "POST"),
        ("/model/delete", {"id": "db-2"}, "POST"),
    ]


def test_delete_model_preserves_ansible_and_manual_models(monkeypatch):
    response = {
        "data": [
            _deployment("ollama/qwen:4b", "config-1", db_model=False),
            _deployment("ollama_chat/qwen:4b", "manual-1", source="manual"),
        ]
    }
    calls = []
    monkeypatch.setattr(models.client, "enabled", lambda: True)
    monkeypatch.setattr(
        models.client,
        "request",
        lambda path, payload=None, method="POST": (
            calls.append((path, payload, method)) or response
        ),
    )

    error = models.delete_ollama_model.execute("qwen:4b")

    assert error is None
    assert calls == [("/v1/model/info", None, "GET")]


@pytest.fixture(autouse=True)
def usecase_components(portal_dependencies):
    global models, portal, ollama
    portal = portal_dependencies
    models = portal_dependencies.llm
    ollama = portal_dependencies.ollama.backend
