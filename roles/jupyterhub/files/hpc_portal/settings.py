"""環境変数からHPCポータルの設定を読む。"""

import json
import os
import re


def _value(name, default=""):
    """環境変数を前後空白を除いて取得する。

    Args:
        name: 環境変数名。
        default: 未設定時の既定値。

    Returns:
        文字列化して前後空白を除いた設定値。
    """
    return os.environ.get(name, default).strip()


def _int(name, default):
    """環境変数を整数として取得する。

    Args:
        name: 環境変数名。
        default: 未設定時の既定値。

    Returns:
        変換済みの整数値。

    Raises:
        ValueError: 環境変数が整数として不正な場合。
    """
    raw = _value(name, str(default))
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer: {raw!r}") from exc


def _bool(name, default):
    """環境変数を真偽値として取得する。

    Args:
        name: 環境変数名。
        default: 未設定時の既定値。

    Returns:
        真偽値。

    Raises:
        ValueError: 環境変数が真偽値として不正な場合。
    """
    raw = _value(name, str(default)).lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean: {raw!r}")


def _json_list(name, default):
    """JSON配列の環境変数を文字列タプルとして取得する。

    Args:
        name: 環境変数名。
        default: 未設定時の既定配列。

    Returns:
        各要素を文字列化したタプル。

    Raises:
        ValueError: 環境変数が文字列配列として不正な場合。
    """
    raw = _value(name, json.dumps(default))
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must be a JSON array: {raw!r}") from exc
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a JSON array: {raw!r}")
    return tuple(str(item) for item in value)


def _required(name):
    """必須の環境変数を取得する。

    Args:
        name: 環境変数名。

    Returns:
        空ではない設定値。

    Raises:
        ValueError: 環境変数が未設定または空の場合。
    """
    value = _value(name)
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _validate_settings():
    """読み込んだ設定値の組み合わせを検証する。

    Raises:
        ValueError: ポート、ドメイン、リソース設定が不正な場合。
    """
    errors = []
    for name, domain in (
        ("HPC_PUBLIC_DOMAIN", HPC_PUBLIC_DOMAIN),
        ("HPC_JOB_DNS_DOMAIN", HPC_JOB_DNS_DOMAIN),
    ):
        if "://" in domain or "/" in domain or any(char.isspace() for char in domain):
            errors.append(f"{name} must be a hostname without scheme or path")
    if HPC_PUBLIC_SCHEME not in {"http", "https"}:
        errors.append("HPC_PUBLIC_SCHEME must be http or https")
    for name, port in (
        ("JUPYTERHUB_PORT", JUPYTERHUB_PORT),
        ("JUPYTERHUB_HUB_PORT", JUPYTERHUB_HUB_PORT),
    ):
        if not 1 <= port <= 65535:
            errors.append(f"{name} must be between 1 and 65535")
    if HPC_GPU_COUNT < 0:
        errors.append("HPC_GPU_COUNT must be zero or greater")
    if not HPC_OLLAMA_ALLOWED_CPUS:
        errors.append("HPC_OLLAMA_ALLOWED_CPUS must not be empty")
    elif any(not value.isdigit() or int(value) <= 0 for value in HPC_OLLAMA_ALLOWED_CPUS):
        errors.append("HPC_OLLAMA_ALLOWED_CPUS must contain positive integers")
    if not HPC_OLLAMA_ALLOWED_MEMORY:
        errors.append("HPC_OLLAMA_ALLOWED_MEMORY must not be empty")
    elif any(
        re.fullmatch(r"[1-9][0-9]*[KMGTP](?:B)?", value, re.IGNORECASE) is None
        for value in HPC_OLLAMA_ALLOWED_MEMORY
    ):
        errors.append("HPC_OLLAMA_ALLOWED_MEMORY contains an invalid Slurm memory value")
    if HPC_OLLAMA_DEFAULT_CPUS not in HPC_OLLAMA_ALLOWED_CPUS:
        errors.append("HPC_OLLAMA_DEFAULT_CPUS must be in HPC_OLLAMA_ALLOWED_CPUS")
    if HPC_OLLAMA_DEFAULT_MEMORY not in HPC_OLLAMA_ALLOWED_MEMORY:
        errors.append("HPC_OLLAMA_DEFAULT_MEMORY must be in HPC_OLLAMA_ALLOWED_MEMORY")
    for name, allowed, default in (
        ("PARALLEL", HPC_OLLAMA_ALLOWED_PARALLEL, HPC_OLLAMA_DEFAULT_PARALLEL),
        (
            "MAX_LOADED_MODELS",
            HPC_OLLAMA_ALLOWED_MAX_LOADED_MODELS,
            HPC_OLLAMA_DEFAULT_MAX_LOADED_MODELS,
        ),
        (
            "CONTEXT_LENGTH",
            HPC_OLLAMA_ALLOWED_CONTEXT_LENGTHS,
            HPC_OLLAMA_DEFAULT_CONTEXT_LENGTH,
        ),
        ("MAX_QUEUE", HPC_OLLAMA_ALLOWED_MAX_QUEUE, HPC_OLLAMA_DEFAULT_MAX_QUEUE),
    ):
        if not allowed or any(not value.isdigit() or int(value) <= 0 for value in allowed):
            errors.append(f"HPC_OLLAMA_ALLOWED_{name} must contain positive integers")
        if default not in allowed:
            errors.append(f"HPC_OLLAMA_DEFAULT_{name} must be in its allowlist")
    if HPC_OLLAMA_DEFAULT_KV_CACHE_TYPE not in HPC_OLLAMA_ALLOWED_KV_CACHE_TYPES:
        errors.append("HPC_OLLAMA_DEFAULT_KV_CACHE_TYPE must be in its allowlist")
    if HPC_OLLAMA_DEFAULT_KEEP_ALIVE not in HPC_OLLAMA_ALLOWED_KEEP_ALIVE:
        errors.append("HPC_OLLAMA_DEFAULT_KEEP_ALIVE must be in its allowlist")
    try:
        ollama_port = int(HPC_OLLAMA_PORT)
        if not 1 <= ollama_port <= 65535:
            raise ValueError
    except ValueError:
        errors.append("HPC_OLLAMA_PORT must be between 1 and 65535")
    if not HPC_SEARXNG_QUERY_URL.startswith("http://127.0.0.1:"):
        errors.append("HPC_SEARXNG_QUERY_URL must use the local 127.0.0.1 service")
    if "<query>" not in HPC_SEARXNG_QUERY_URL:
        errors.append("HPC_SEARXNG_QUERY_URL must contain <query>")
    if not 1 <= OPENWEBUI_WEB_SEARCH_RESULT_COUNT <= 10:
        errors.append("OPENWEBUI_WEB_SEARCH_RESULT_COUNT must be between 1 and 10")
    if OPENWEBUI_WEB_SEARCH_CONCURRENT_REQUESTS < 1:
        errors.append("OPENWEBUI_WEB_SEARCH_CONCURRENT_REQUESTS must be positive")
    if OPENWEBUI_WEB_LOADER_CONCURRENT_REQUESTS < 1:
        errors.append("OPENWEBUI_WEB_LOADER_CONCURRENT_REQUESTS must be positive")
    if OPENWEBUI_WEB_FETCH_MAX_CONTENT_LENGTH < 1:
        errors.append("OPENWEBUI_WEB_FETCH_MAX_CONTENT_LENGTH must be positive")
    if errors:
        raise ValueError("Invalid HPC portal configuration: " + "; ".join(errors))


HPC_PUBLIC_DOMAIN = _required("HPC_PUBLIC_DOMAIN")
HPC_JOB_DNS_DOMAIN = _required("HPC_JOB_DNS_DOMAIN")
HPC_PUBLIC_SCHEME = _value("HPC_PUBLIC_SCHEME", "https")
SLURM_NODE_NAME = _required("HPC_SLURM_NODE_NAME")
JUPYTERHUB_PORT = _int("JUPYTERHUB_PORT", 8000)
JUPYTERHUB_HUB_PORT = _int("JUPYTERHUB_HUB_PORT", 8081)
HPC_PORTAL_ADMIN_USERS = set(_json_list("HPC_PORTAL_ADMIN_USERS", []))
HPC_PORTAL_PROTECTED_USERS = set(_json_list("HPC_PORTAL_PROTECTED_USERS", ["root"]))
HPC_PORTAL_USER_MIN_UID = _int("HPC_PORTAL_USER_MIN_UID", 1000)
HPC_PORTAL_GRANT_SUDO = _bool("HPC_PORTAL_GRANT_SUDO", False)
HPC_PORTAL_SUDO_GROUP = _value("HPC_PORTAL_SUDO_GROUP", "sudo")
HPC_GPU_COUNT = _int("HPC_GPU_COUNT", 0)
HPC_OLLAMA_ALLOWED_CPUS = _json_list("HPC_OLLAMA_ALLOWED_CPUS", [4, 8, 12, 16, 20])
HPC_OLLAMA_ALLOWED_MEMORY = _json_list("HPC_OLLAMA_ALLOWED_MEMORY", ["16G", "32G", "64G", "96G", "112G"])
HPC_OLLAMA_DEFAULT_CPUS = _value("HPC_OLLAMA_DEFAULT_CPUS", "8")
HPC_OLLAMA_DEFAULT_MEMORY = _value("HPC_OLLAMA_DEFAULT_MEMORY", "64G")
HPC_OLLAMA_ALLOWED_PARALLEL = _json_list("HPC_OLLAMA_ALLOWED_PARALLEL", [1, 2, 4])
HPC_OLLAMA_DEFAULT_PARALLEL = _value("HPC_OLLAMA_DEFAULT_PARALLEL", "2")
HPC_OLLAMA_ALLOWED_MAX_LOADED_MODELS = _json_list(
    "HPC_OLLAMA_ALLOWED_MAX_LOADED_MODELS", [1, 2, 3]
)
HPC_OLLAMA_DEFAULT_MAX_LOADED_MODELS = _value(
    "HPC_OLLAMA_DEFAULT_MAX_LOADED_MODELS", "2"
)
HPC_OLLAMA_ALLOWED_CONTEXT_LENGTHS = _json_list(
    "HPC_OLLAMA_ALLOWED_CONTEXT_LENGTHS", [32768, 65536, 131072, 262144]
)
HPC_OLLAMA_DEFAULT_CONTEXT_LENGTH = _value(
    "HPC_OLLAMA_DEFAULT_CONTEXT_LENGTH", "131072"
)
HPC_OLLAMA_ALLOWED_KV_CACHE_TYPES = _json_list(
    "HPC_OLLAMA_ALLOWED_KV_CACHE_TYPES", ["f16", "q8_0", "q4_0"]
)
HPC_OLLAMA_DEFAULT_KV_CACHE_TYPE = _value(
    "HPC_OLLAMA_DEFAULT_KV_CACHE_TYPE", "q8_0"
)
HPC_OLLAMA_ALLOWED_KEEP_ALIVE = _json_list(
    "HPC_OLLAMA_ALLOWED_KEEP_ALIVE", ["5m", "30m", "1h", "-1"]
)
HPC_OLLAMA_DEFAULT_KEEP_ALIVE = _value("HPC_OLLAMA_DEFAULT_KEEP_ALIVE", "30m")
HPC_OLLAMA_ALLOWED_MAX_QUEUE = _json_list(
    "HPC_OLLAMA_ALLOWED_MAX_QUEUE", [32, 64, 128, 256]
)
HPC_OLLAMA_DEFAULT_MAX_QUEUE = _value("HPC_OLLAMA_DEFAULT_MAX_QUEUE", "64")
HPC_OLLAMA_DEFAULT_FLASH_ATTENTION = _bool(
    "HPC_OLLAMA_DEFAULT_FLASH_ATTENTION", True
)
HPC_OLLAMA_MODELS_DIR = _value("HPC_OLLAMA_MODELS_DIR", "/srv/ollama/models")
HPC_OLLAMA_PORT = _value("HPC_OLLAMA_PORT", "11434")
HPC_OLLAMA_RUNTIME = _value("HPC_OLLAMA_RUNTIME", "INFINITE")
HPC_BATCH_EXECHOST_EXP = _value("HPC_BATCH_EXECHOST_EXP", "127.0.0.1")
HPC_OPENWEBUI_VERSION = _value("HPC_OPENWEBUI_VERSION", "unknown")
HPC_OLLAMA_VERSION = _value("HPC_OLLAMA_VERSION", "unknown")
HPC_JUPYTER_UBUNTU_VERSION = _value("HPC_JUPYTER_UBUNTU_VERSION", "unknown")
HPC_SEARXNG_QUERY_URL = _value(
    "HPC_SEARXNG_QUERY_URL", "http://127.0.0.1:8888/search?q=<query>"
)
OPENWEBUI_WEB_SEARCH_RESULT_COUNT = _int("OPENWEBUI_WEB_SEARCH_RESULT_COUNT", 6)
OPENWEBUI_WEB_SEARCH_CONCURRENT_REQUESTS = _int(
    "OPENWEBUI_WEB_SEARCH_CONCURRENT_REQUESTS", 1
)
OPENWEBUI_WEB_LOADER_CONCURRENT_REQUESTS = _int(
    "OPENWEBUI_WEB_LOADER_CONCURRENT_REQUESTS", 2
)
OPENWEBUI_WEB_FETCH_MAX_CONTENT_LENGTH = _int(
    "OPENWEBUI_WEB_FETCH_MAX_CONTENT_LENGTH", 12000
)

_validate_settings()
