"""OllamaとLiteLLM間のモデル同期仕様を検証する。"""

from unittest.mock import ANY

import pytest

from hpc_portal import ollama
from hpc_portal.litellm import models


def test_model_records_accepts_supported_response_shapes():
    response = {
        "data": ["plain-model", {"id": "id-model"}],
        "models": [{"model_name": "named-model"}],
        "model_list": [{"litellm_params": {"model": "ollama/qwen"}}],
    }

    records = list(models._hpc_litellm_model_records(response))

    assert {record.get("id") for record in records if record.get("id")} == {
        "plain-model",
        "id-model",
    }
    assert any(record.get("model_name") == "named-model" for record in records)


def test_list_models_deduplicates_and_sorts(monkeypatch):
    monkeypatch.setattr(models, "_hpc_litellm_enabled", lambda: True)
    monkeypatch.setattr(
        models,
        "_hpc_litellm_request",
        lambda path, method="POST": {
            "data": [
                {"id": "z-model", "owned_by": "team"},
                {"model_name": "a-model", "provider": "ollama"},
                {"id": "z-model", "owned_by": "duplicate"},
                "",
            ]
        },
    )

    listed, error = models._hpc_litellm_list_models()

    assert error is None
    assert listed == [
        {"id": "a-model", "owned_by": "ollama"},
        {"id": "z-model", "owned_by": "team"},
    ]


@pytest.mark.parametrize("model", ["", "bad model", "$(id)", "a" * 129])
def test_register_model_rejects_invalid_name_without_external_calls(monkeypatch, model):
    monkeypatch.setattr(models, "_hpc_litellm_request", lambda *args, **kwargs: pytest.fail("通信禁止"))

    result, error = models._hpc_litellm_register_ollama_model(model)

    assert result is None
    assert error


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
    monkeypatch.setattr(ollama, "_hpc_ollama_has_model", lambda model: (True, None))
    monkeypatch.setattr(
        ollama,
        "_hpc_ollama_model_supports_tools",
        lambda model: (supports_tools, None),
    )


def test_register_model_returns_already_registered_for_portal_chat_backend(monkeypatch):
    monkeypatch.setattr(models, "_hpc_litellm_enabled", lambda: True)
    _mock_ollama_model(monkeypatch)
    monkeypatch.setattr(
        models,
        "_hpc_litellm_model_info",
        lambda: ({"data": [_deployment()]}, None),
    )
    monkeypatch.setattr(models, "_hpc_litellm_request", lambda *args, **kwargs: pytest.fail("作成禁止"))

    result, error = models._hpc_litellm_register_ollama_model("qwen:4b")

    assert error is None
    assert result["state"] == "already_registered"


def test_register_model_does_not_duplicate_manual_chat_backend(monkeypatch):
    manual = _deployment(source="manual")
    monkeypatch.setattr(models, "_hpc_litellm_enabled", lambda: True)
    _mock_ollama_model(monkeypatch)
    monkeypatch.setattr(
        models,
        "_hpc_litellm_model_info",
        lambda: ({"data": [manual]}, None),
    )
    monkeypatch.setattr(
        models,
        "_hpc_litellm_request",
        lambda *args, **kwargs: pytest.fail("作成・削除禁止"),
    )

    result, error = models._hpc_litellm_register_ollama_model("qwen:4b")

    assert error is None
    assert result["state"] == "already_registered"


def test_register_model_refuses_to_mix_manual_legacy_backend(monkeypatch):
    manual_legacy = _deployment("ollama/qwen:4b", source="manual")
    monkeypatch.setattr(models, "_hpc_litellm_enabled", lambda: True)
    _mock_ollama_model(monkeypatch)
    monkeypatch.setattr(
        models,
        "_hpc_litellm_model_info",
        lambda: ({"data": [manual_legacy]}, None),
    )
    monkeypatch.setattr(
        models,
        "_hpc_litellm_request",
        lambda *args, **kwargs: pytest.fail("作成・削除禁止"),
    )

    result, error = models._hpc_litellm_register_ollama_model("qwen:4b")

    assert result is None
    assert "LiteLLM管理画面" in error


def test_register_model_creates_db_record_and_verifies_it(monkeypatch):
    calls = []
    responses = iter([({"data": []}, None), ({"data": [_deployment()]}, None)])
    monkeypatch.setattr(models, "_hpc_litellm_enabled", lambda: True)
    _mock_ollama_model(monkeypatch, supports_tools=True)
    monkeypatch.setattr(models, "_hpc_litellm_model_info", lambda: next(responses))
    monkeypatch.setattr(
        models,
        "_hpc_litellm_request",
        lambda path, payload: calls.append((path, payload)) or {"ok": True},
    )

    result, error = models._hpc_litellm_register_ollama_model("qwen:4b")

    assert error is None
    assert result["state"] == "registered"
    assert calls[0][0] == "/model/new"
    assert calls[0][1]["model_name"] == "qwen:4b"
    assert calls[0][1]["litellm_params"]["model"] == "ollama_chat/qwen:4b"
    assert calls[0][1]["model_info"]["supports_function_calling"] is True


def test_register_model_verifies_chat_backend_before_deleting_legacy(monkeypatch):
    calls = []
    legacy = _deployment("ollama/qwen:4b", "legacy-1")
    correct = _deployment("ollama_chat/qwen:4b", "chat-1")
    responses = iter([({"data": [legacy]}, None), ({"data": [legacy, correct]}, None)])
    monkeypatch.setattr(models, "_hpc_litellm_enabled", lambda: True)
    _mock_ollama_model(monkeypatch)
    monkeypatch.setattr(models, "_hpc_litellm_model_info", lambda: next(responses))
    monkeypatch.setattr(
        models,
        "_hpc_litellm_request",
        lambda path, payload: calls.append((path, payload)) or {"ok": True},
    )

    result, error = models._hpc_litellm_register_ollama_model("qwen:4b")

    assert error is None
    assert result["migrated"] == 1
    assert calls == [
        ("/model/new", ANY),
        ("/model/delete", {"id": "legacy-1"}),
    ]


def test_register_model_preserves_legacy_when_chat_backend_verification_fails(monkeypatch):
    calls = []
    legacy = _deployment("ollama/qwen:4b", "legacy-1")
    responses = iter([({"data": [legacy]}, None), ({"data": [legacy]}, None)])
    monkeypatch.setattr(models, "_hpc_litellm_enabled", lambda: True)
    _mock_ollama_model(monkeypatch)
    monkeypatch.setattr(models, "_hpc_litellm_model_info", lambda: next(responses))
    monkeypatch.setattr(
        models,
        "_hpc_litellm_request",
        lambda path, payload: calls.append((path, payload)) or {"ok": True},
    )

    result, error = models._hpc_litellm_register_ollama_model("qwen:4b")

    assert result is None
    assert "確認できません" in error
    assert [path for path, _payload in calls] == ["/model/new"]


def test_register_model_does_not_create_when_ollama_model_is_missing(monkeypatch):
    monkeypatch.setattr(models, "_hpc_litellm_enabled", lambda: True)
    monkeypatch.setattr(ollama, "_hpc_ollama_has_model", lambda model: (False, "missing"))
    monkeypatch.setattr(models, "_hpc_litellm_request", lambda *args, **kwargs: pytest.fail("通信禁止"))

    result, error = models._hpc_litellm_register_ollama_model("qwen:4b")

    assert result is None
    assert error == "missing"


def test_register_model_redacts_key_from_api_error(monkeypatch):
    monkeypatch.setattr(models, "_hpc_litellm_enabled", lambda: True)
    _mock_ollama_model(monkeypatch)
    monkeypatch.setattr(models, "_hpc_litellm_model_info", lambda: ({"data": []}, None))
    monkeypatch.setattr(
        models,
        "_hpc_litellm_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("bad sk-private-key")),
    )

    result, error = models._hpc_litellm_register_ollama_model("qwen:4b")

    assert result is None
    assert "sk-private" not in error
    assert "REDACTED" in error


def test_delete_model_deletes_only_matching_db_deployments_once(monkeypatch):
    calls = []
    response = {
        "data": [
            _deployment("ollama/qwen:4b", "db-1"),
            _deployment("ollama/qwen:4b", "db-1"),
            _deployment("ollama_chat/qwen:4b", "db-2"),
            _deployment("ollama_chat/qwen:4b", "manual-1", source="manual"),
            {"model_name": "other", "litellm_params": {"model": "ollama/other"}, "model_info": {"id": "db-2", "db_model": True}},
        ]
    }

    def fake_request(path, payload=None, method="POST"):
        """一覧取得結果を返し、削除要求を記録する。"""
        calls.append((path, payload, method))
        return response if path == "/v1/model/info" else {"ok": True}

    monkeypatch.setattr(models, "_hpc_litellm_enabled", lambda: True)
    monkeypatch.setattr(models, "_hpc_litellm_request", fake_request)

    assert models._hpc_litellm_delete_ollama_model("qwen:4b") is None
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
    monkeypatch.setattr(models, "_hpc_litellm_enabled", lambda: True)
    monkeypatch.setattr(
        models,
        "_hpc_litellm_request",
        lambda path, payload=None, method="POST": calls.append((path, payload, method))
        or response,
    )

    error = models._hpc_litellm_delete_ollama_model("qwen:4b")

    assert error is None
    assert calls == [("/v1/model/info", None, "GET")]


def test_sync_all_models_reports_changed_and_failed(monkeypatch):
    monkeypatch.setattr(
        ollama,
        "_hpc_ollama_model_names",
        lambda: (["a:1b", "b:2b"], None),
    )
    monkeypatch.setattr(
        models,
        "_hpc_litellm_register_ollama_model",
        lambda model: (
            ({"model": model, "state": "registered"}, None)
            if model == "a:1b"
            else (None, "capability failed")
        ),
    )

    result, error = models._hpc_litellm_sync_ollama_models()

    assert error is None
    assert result["total"] == 2
    assert result["changed"] == 1
    assert result["failed"] == 1
