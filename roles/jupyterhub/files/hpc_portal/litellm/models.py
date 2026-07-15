"""LiteLLMのOllamaモデル登録・削除・一覧取得を提供する。"""

import re
import threading

from ..common import HPC_LITELLM_LOG, HPC_OLLAMA_API_BASE
from .client import (
    _hpc_litellm_enabled,
    _hpc_litellm_request,
    _hpc_safe_litellm_error,
)

_HPC_LITELLM_MODEL_LOCKS: dict[str, threading.Lock] = {}
_HPC_LITELLM_MODEL_LOCKS_GUARD = threading.Lock()

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
        models.append({
            "id": model_id,
            "owned_by": str(record.get("owned_by") or record.get("provider") or "").strip(),
        })
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

def _hpc_litellm_register_ollama_model(model: str) -> tuple[dict | None, str | None]:
    """OllamaモデルをLiteLLMへ重複なく登録する。

    LiteLLMのモデル一覧に同名モデルがあれば登録済みとして扱う。未登録の場合は
    Ollamaのモデル名を公開名にし、DB保存される ``/model/new`` へ追加する。

    Args:
        model: Ollamaに登録済みのモデル名。

    Returns:
        ``(登録状態, エラー)``。登録状態の ``state`` は ``registered`` または
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
        from ..ollama import _hpc_ollama_has_model

        exists, ollama_err = _hpc_ollama_has_model(model)
        if ollama_err or not exists:
            return None, ollama_err or f"Ollamaにモデル {model} がありません"
        models, err = _hpc_litellm_list_models()
        if err:
            return None, err
        if any(item.get("id") == model for item in models):
            return {
                "state": "already_registered",
                "model": model,
                "message": "LiteLLM登録済み",
            }, None

        payload = {
            "model_name": model,
            "litellm_params": {
                "model": f"ollama/{model}",
                "api_base": HPC_OLLAMA_API_BASE,
            },
            "model_info": {
                "source": "hpc-portal-ollama",
            },
        }
        try:
            _hpc_litellm_request("/model/new", payload)
        except RuntimeError as exc:
            return None, _hpc_safe_litellm_error(exc)

        models, err = _hpc_litellm_list_models()
        if err:
            return None, f"登録後の確認に失敗しました: {err}"
        if not any(item.get("id") == model for item in models):
            return None, "LiteLLMへ追加しましたが、モデル一覧で確認できませんでした"
        HPC_LITELLM_LOG.info(
            "action=model_register model=%s backend=%s result=ok",
            model,
            f"ollama/{model}",
        )
        return {
            "state": "registered",
            "model": model,
            "message": "LiteLLMへ自動登録しました",
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
        deployment_ids = []
        config_model_found = False
        for record in _hpc_litellm_model_records(response):
            litellm_params = record.get("litellm_params") or {}
            model_info = record.get("model_info") or {}
            if not isinstance(litellm_params, dict) or not isinstance(model_info, dict):
                continue
            if str(record.get("model_name") or "") != model:
                continue
            if str(litellm_params.get("model") or "") != f"ollama/{model}":
                continue
            model_id = str(model_info.get("id") or "").strip()
            if model_id and bool(model_info.get("db_model")):
                deployment_ids.append(model_id)
            else:
                config_model_found = True

        if config_model_found:
            return "Ansible設定由来のLiteLLMモデルはポータルから削除できません"

        for model_id in dict.fromkeys(deployment_ids):
            try:
                _hpc_litellm_request("/model/delete", {"id": model_id})
            except RuntimeError as exc:
                return _hpc_safe_litellm_error(exc)
        if deployment_ids:
            HPC_LITELLM_LOG.info(
                "action=model_delete model=%s deployments=%d result=ok",
                model,
                len(set(deployment_ids)),
            )
        return None
