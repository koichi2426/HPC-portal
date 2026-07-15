"""LiteLLM統合機能を責務別モジュールから公開する。"""

from .client import (
    _hpc_litellm_enabled,
    _hpc_litellm_request,
    _hpc_log_litellm_action,
    _hpc_safe_litellm_error,
)
from .keys import (
    _hpc_litellm_admin_set_api_access,
    _hpc_litellm_delete_user_keys,
    _hpc_litellm_generate_key,
    _hpc_litellm_regenerate_own_key,
    _hpc_litellm_user_external_api_state,
)
from .models import (
    _hpc_litellm_delete_ollama_model,
    _hpc_litellm_list_models,
    _hpc_litellm_register_ollama_model,
)
from .openwebui import _hpc_litellm_get_openwebui_key
from .users import _hpc_litellm_user_admin_disabled

__all__ = [name for name in globals() if name.startswith("_hpc_")]

