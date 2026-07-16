"""OllamaとLiteLLM間のモデル同期仕様を検証する。"""

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


def test_register_model_returns_already_registered_without_create(monkeypatch):
    monkeypatch.setattr(models, "_hpc_litellm_enabled", lambda: True)
    monkeypatch.setattr(ollama, "_hpc_ollama_has_model", lambda model: (True, None))
    monkeypatch.setattr(models, "_hpc_litellm_list_models", lambda: ([{"id": "qwen:4b"}], None))
    monkeypatch.setattr(models, "_hpc_litellm_request", lambda *args, **kwargs: pytest.fail("作成禁止"))

    result, error = models._hpc_litellm_register_ollama_model("qwen:4b")

    assert error is None
    assert result["state"] == "already_registered"


def test_register_model_creates_db_record_and_verifies_it(monkeypatch):
    calls = []
    listings = iter([([], None), ([{"id": "qwen:4b"}], None)])
    monkeypatch.setattr(models, "_hpc_litellm_enabled", lambda: True)
    monkeypatch.setattr(ollama, "_hpc_ollama_has_model", lambda model: (True, None))
    monkeypatch.setattr(models, "_hpc_litellm_list_models", lambda: next(listings))
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
    assert calls[0][1]["litellm_params"]["model"] == "ollama/qwen:4b"


def test_register_model_does_not_create_when_ollama_model_is_missing(monkeypatch):
    monkeypatch.setattr(models, "_hpc_litellm_enabled", lambda: True)
    monkeypatch.setattr(ollama, "_hpc_ollama_has_model", lambda model: (False, "missing"))
    monkeypatch.setattr(models, "_hpc_litellm_request", lambda *args, **kwargs: pytest.fail("通信禁止"))

    result, error = models._hpc_litellm_register_ollama_model("qwen:4b")

    assert result is None
    assert error == "missing"


def test_register_model_redacts_key_from_api_error(monkeypatch):
    monkeypatch.setattr(models, "_hpc_litellm_enabled", lambda: True)
    monkeypatch.setattr(ollama, "_hpc_ollama_has_model", lambda model: (True, None))
    monkeypatch.setattr(models, "_hpc_litellm_list_models", lambda: ([], None))
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
            {"model_name": "qwen:4b", "litellm_params": {"model": "ollama/qwen:4b"}, "model_info": {"id": "db-1", "db_model": True}},
            {"model_name": "qwen:4b", "litellm_params": {"model": "ollama/qwen:4b"}, "model_info": {"id": "db-1", "db_model": True}},
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
    ]


def test_delete_model_refuses_ansible_config_model(monkeypatch):
    response = {
        "data": [
            {"model_name": "qwen:4b", "litellm_params": {"model": "ollama/qwen:4b"}, "model_info": {"id": "config-1", "db_model": False}}
        ]
    }
    monkeypatch.setattr(models, "_hpc_litellm_enabled", lambda: True)
    monkeypatch.setattr(models, "_hpc_litellm_request", lambda path, payload=None, method="POST": response)

    error = models._hpc_litellm_delete_ollama_model("qwen:4b")

    assert "Ansible設定由来" in error

