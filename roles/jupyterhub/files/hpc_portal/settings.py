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
    try:
        ollama_port = int(HPC_OLLAMA_PORT)
        if not 1 <= ollama_port <= 65535:
            raise ValueError
    except ValueError:
        errors.append("HPC_OLLAMA_PORT must be between 1 and 65535")
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
HPC_PORTAL_GRANT_SUDO = _bool("HPC_PORTAL_GRANT_SUDO", True)
HPC_PORTAL_SUDO_GROUP = _value("HPC_PORTAL_SUDO_GROUP", "sudo")
HPC_GPU_COUNT = _int("HPC_GPU_COUNT", 0)
HPC_OLLAMA_ALLOWED_CPUS = _json_list("HPC_OLLAMA_ALLOWED_CPUS", [4, 8, 12, 16, 20])
HPC_OLLAMA_ALLOWED_MEMORY = _json_list("HPC_OLLAMA_ALLOWED_MEMORY", ["16G", "32G", "64G", "96G", "112G"])
HPC_OLLAMA_DEFAULT_CPUS = _value("HPC_OLLAMA_DEFAULT_CPUS", "4")
HPC_OLLAMA_DEFAULT_MEMORY = _value("HPC_OLLAMA_DEFAULT_MEMORY", "16G")
HPC_OLLAMA_MODELS_DIR = _value("HPC_OLLAMA_MODELS_DIR", "/srv/ollama/models")
HPC_OLLAMA_PORT = _value("HPC_OLLAMA_PORT", "11434")
HPC_OLLAMA_RUNTIME = _value("HPC_OLLAMA_RUNTIME", "INFINITE")
HPC_BATCH_EXECHOST_EXP = _value("HPC_BATCH_EXECHOST_EXP", "127.0.0.1")

_validate_settings()
