"""LiteLLMのOllamaモデル登録・削除・一覧取得を提供する。"""

import re
import threading

from ..common import HPC_LITELLM_LOG, HPC_OLLAMA_API_BASE
from ..schemas import HpcLlmModel
from .client import (
    _hpc_litellm_enabled,
    _hpc_litellm_request,
    _hpc_safe_litellm_error,
)

_HPC_LITELLM_MODEL_LOCKS: dict[str, threading.Lock] = {}
_HPC_LITELLM_MODEL_LOCKS_GUARD = threading.Lock()
_HPC_LITELLM_OLLAMA_SOURCE = "hpc-portal-ollama"
_HPC_LITELLM_OLLAMA_BACKENDS = ("ollama_chat/", "ollama/")

def _hpc_litellm_model_records(value):
    """LiteLLM の model 一覧レスポンスから model record を取り出す。

    Args:
        value: LiteLLM API から返った JSON 互換オブジェクト。

    Yields:
        model ID を含む可能性がある dict を順に返す generator。
    """
    if isinstance(value, dict):
        for key in ("data", "models", "model_list"):
            if key in value:
                yield from _hpc_litellm_model_records(value[key])
        if any(k in value for k in ("id", "model_name", "litellm_params")):
            yield value
        return
    if isinstance(value, list):
        for item in value:
            yield from _hpc_litellm_model_records(item)
        return
    if isinstance(value, str) and value.strip():
        yield {"id": value.strip()}

def _hpc_litellm_list_models() -> tuple[list[dict], str | None]:
    """ユーザー画面に表示する LiteLLM model 一覧を取得する。

    LiteLLM の `/models` はバージョンや設定によって response shape が揺れるため、
    複数の候補フィールドから model ID を取り出して重複を除去する。

    Returns:
        1要素目は `id` と `owned_by` を持つ model dict の list。
        2要素目は取得失敗時のエラーメッセージ。成功時は None。
    """
    if not _hpc_litellm_enabled():
        return [], "LiteLLM Admin API が未設定です"
    try:
        data = _hpc_litellm_request("/models", method="GET")
    except RuntimeError as exc:
        return [], str(exc)
    models = []
    seen = set()
    for record in _hpc_litellm_model_records(data):
        model_id = (
            record.get("id")
            or record.get("model_name")
            or record.get("model")
            or ""
        )
        model_id = str(model_id).strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        models.append(
            HpcLlmModel.model_validate(
                {
                    "id": model_id,
                    "owned_by": str(
                        record.get("owned_by") or record.get("provider") or ""
                    ).strip(),
                }
            ).model_dump()
        )
    models.sort(key=lambda item: item["id"])
    return models, None

def _hpc_litellm_model_lock(model: str) -> threading.Lock:
    """モデル単位のLiteLLM登録ロックを返す。

    Args:
        model: Ollamaモデル名。

    Returns:
        同一プロセス内の重複登録を防ぐロック。
    """
    with _HPC_LITELLM_MODEL_LOCKS_GUARD:
        return _HPC_LITELLM_MODEL_LOCKS.setdefault(model, threading.Lock())


def _hpc_litellm_model_info() -> tuple[dict | list | None, str | None]:
    """LiteLLMのdeployment情報を取得する。

    Returns:
        ``(APIレスポンス, エラー)``。取得成功時のエラーはNone。
    """
    try:
        return _hpc_litellm_request("/v1/model/info", method="GET"), None
    except RuntimeError as exc:
        return None, _hpc_safe_litellm_error(exc)


def _hpc_litellm_ollama_deployments(value, model: str) -> list[dict]:
    """指定Ollamaモデルに対応するLiteLLM deploymentを抽出する。

    Args:
        value: ``/v1/model/info`` のJSON互換レスポンス。
        model: 公開モデル名およびOllamaモデル名。

    Returns:
        backend、ID、DB由来か、ポータル管理かを持つdeployment一覧。
    """
    deployments = []
    expected_backends = {prefix + model for prefix in _HPC_LITELLM_OLLAMA_BACKENDS}
    for record in _hpc_litellm_model_records(value):
        litellm_params = record.get("litellm_params") or {}
        model_info = record.get("model_info") or {}
        if not isinstance(litellm_params, dict) or not isinstance(model_info, dict):
            continue
        if str(record.get("model_name") or "") != model:
            continue
        backend = str(litellm_params.get("model") or "")
        if backend not in expected_backends:
            continue
        is_db_model = bool(model_info.get("db_model"))
        source = str(model_info.get("source") or "")
        deployments.append(
            {
                "id": str(model_info.get("id") or "").strip(),
                "backend": backend,
                "db_model": is_db_model,
                "portal_managed": is_db_model
                and source == _HPC_LITELLM_OLLAMA_SOURCE,
                "supports_function_calling": bool(
                    model_info.get("supports_function_calling")
                ),
            }
        )
    return deployments


def _hpc_litellm_delete_deployments(deployment_ids: list[str]) -> str | None:
    """LiteLLMのDB deploymentをID指定で削除する。

    Args:
        deployment_ids: 削除するLiteLLM model ID。

    Returns:
        正常時はNone、失敗時は安全化したエラーメッセージ。
    """
    for model_id in dict.fromkeys(item for item in deployment_ids if item):
        try:
            _hpc_litellm_request("/model/delete", {"id": model_id})
        except RuntimeError as exc:
            return _hpc_safe_litellm_error(exc)
    return None

def _hpc_litellm_register_ollama_model(model: str) -> tuple[dict | None, str | None]:
    """OllamaモデルをLiteLLMのchat backendへ安全に同期する。

    正しい ``ollama_chat/`` deploymentを作成・確認してから、旧
    ``ollama/`` deploymentを削除する。ポータル管理外の設定モデルや手動登録
    モデルは変更しない。

    Args:
        model: Ollamaに登録済みのモデル名。

    Returns:
        ``(同期状態, エラー)``。状態は ``registered`` または
        ``already_registered``。
    """
    model = str(model or "").strip()
    if not model:
        return None, "モデル名が必要です"
    if re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", model) is None:
        return None, "モデル名に使用できない文字が含まれています"
    if not _hpc_litellm_enabled():
        return None, "LiteLLM Admin API が未設定です"

    with _hpc_litellm_model_lock(model):
        # 削除とpull完了が競合した場合に、Ollamaにないモデルを再登録しない。
        from ..ollama import (
            _hpc_ollama_has_model,
            _hpc_ollama_model_supports_tools,
        )

        exists, ollama_err = _hpc_ollama_has_model(model)
        if ollama_err or not exists:
            return None, ollama_err or f"Ollamaにモデル {model} がありません"
        supports_tools, capability_err = _hpc_ollama_model_supports_tools(model)
        if capability_err or supports_tools is None:
            return None, capability_err or "Ollamaモデルの機能を確認できません"

        response, err = _hpc_litellm_model_info()
        if err:
            return None, err
        deployments = _hpc_litellm_ollama_deployments(response, model)
        target_backend = f"ollama_chat/{model}"
        correct = [
            item
            for item in deployments
            if item["backend"] == target_backend
        ]
        legacy = [
            item
            for item in deployments
            if item["portal_managed"] and item["backend"] == f"ollama/{model}"
        ]
        unmanaged_legacy = [
            item
            for item in deployments
            if not item["portal_managed"] and item["backend"] == f"ollama/{model}"
        ]
        if unmanaged_legacy:
            return None, (
                "同名の設定ファイル由来または手動登録されたollama/モデルがあるため、"
                "LiteLLM管理画面で確認してください"
            )

        created = False
        if not correct:
            payload = {
                "model_name": model,
                "litellm_params": {
                    "model": target_backend,
                    "api_base": HPC_OLLAMA_API_BASE,
                },
                "model_info": {
                    "source": _HPC_LITELLM_OLLAMA_SOURCE,
                    "supports_function_calling": supports_tools,
                },
            }
            try:
                _hpc_litellm_request("/model/new", payload)
            except RuntimeError as exc:
                return None, _hpc_safe_litellm_error(exc)
            created = True

        # 作成後の再取得で正しいDB deploymentを確認するまで旧設定は残す。
        verified_response, err = _hpc_litellm_model_info()
        if err:
            return None, f"登録後の確認に失敗しました: {err}"
        verified = _hpc_litellm_ollama_deployments(verified_response, model)
        verified_correct = [
            item
            for item in verified
            if item["backend"] == target_backend
        ]
        if not verified_correct:
            return None, "LiteLLMへ追加しましたが、正しい接続方式を確認できませんでした"

        verified_legacy = [
            item
            for item in verified
            if item["portal_managed"] and item["backend"] == f"ollama/{model}"
        ]
        delete_err = _hpc_litellm_delete_deployments(
            [item["id"] for item in verified_legacy]
        )
        if delete_err:
            return None, "旧LiteLLMモデルの削除に失敗しました: " + delete_err

        migrated = len({item["id"] for item in legacy if item["id"]})
        HPC_LITELLM_LOG.info(
            "action=model_sync model=%s backend=%s supports_tools=%s migrated=%d result=ok",
            model,
            target_backend,
            supports_tools,
            migrated,
        )
        return {
            "state": "registered" if created or migrated else "already_registered",
            "model": model,
            "backend": target_backend,
            "supports_tools": supports_tools,
            "migrated": migrated,
            "message": "LiteLLMへ同期しました"
            if created or migrated
            else "LiteLLM同期済み",
        }, None


def _hpc_litellm_sync_ollama_models() -> tuple[dict | None, str | None]:
    """Ollamaに存在する全モデルをLiteLLMへ同期する。

    Returns:
        ``(モデル別結果と件数, エラー)``。一覧取得失敗時のみ全体エラーを返す。
    """
    from ..ollama import _hpc_ollama_model_names

    model_names, err = _hpc_ollama_model_names()
    if err:
        return None, err
    results = []
    for model in model_names:
        state, model_err = _hpc_litellm_register_ollama_model(model)
        if model_err:
            results.append({"model": model, "state": "failed", "message": model_err})
        else:
            results.append(state or {"model": model, "state": "failed"})
    failed = sum(item.get("state") == "failed" for item in results)
    changed = sum(item.get("state") == "registered" for item in results)
    HPC_LITELLM_LOG.info(
        "action=models_sync total=%d changed=%d failed=%d result=%s",
        len(results),
        changed,
        failed,
        "partial" if failed else "ok",
    )
    return {
        "total": len(results),
        "changed": changed,
        "failed": failed,
        "results": results,
    }, None

def _hpc_litellm_delete_ollama_model(model: str) -> str | None:
    """Ollamaモデルに対応するLiteLLMのDBモデルを削除する。

    Args:
        model: Ollamaから削除するモデル名。

    Returns:
        正常または該当モデルなしならNone、失敗時はエラーメッセージ。
    """
    model = str(model or "").strip()
    if not model:
        return "モデル名が必要です"
    if re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", model) is None:
        return "モデル名に使用できない文字が含まれています"
    if not _hpc_litellm_enabled():
        return "LiteLLM Admin API が未設定です"

    with _hpc_litellm_model_lock(model):
        try:
            response = _hpc_litellm_request("/v1/model/info", method="GET")
        except RuntimeError as exc:
            return _hpc_safe_litellm_error(exc)
        deployments = _hpc_litellm_ollama_deployments(response, model)
        deployment_ids = [
            item["id"] for item in deployments if item["portal_managed"] and item["id"]
        ]
        delete_err = _hpc_litellm_delete_deployments(deployment_ids)
        if delete_err:
            return delete_err
        if deployment_ids:
            HPC_LITELLM_LOG.info(
                "action=model_delete model=%s deployments=%d result=ok",
                model,
                len(set(deployment_ids)),
            )
        return None
