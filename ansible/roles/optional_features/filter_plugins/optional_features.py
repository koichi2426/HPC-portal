"""任意機能の設定不足と明示的な無効化を区別し、導入済み設定を維持する。"""

import json
import re

DEFAULTS = {
    "HPC_EXTERNAL_API_ENABLED": "false",
    "HPC_CLOUDFLARE_ACCOUNT_ID": "",
    "HPC_CLOUDFLARE_API_TOKEN": "",
    "HPC_EXTERNAL_API_ACCESS_ISSUER": "",
    "HPC_EXTERNAL_API_STATE_DIR": "/var/lib/jupyterhub/external-api-v2",
    "HPC_EXTERNAL_API_TOKEN_LIMIT": "50",
    "HPC_EXTERNAL_API_PORT_START": "20000",
    "HPC_EXTERNAL_API_PORT_END": "29999",
    "HPC_EXTERNAL_API_RESERVED_PORTS": "'[22, 80, 443, 4000, 5432, 8000, 8001, 8081, 8888, 8890, 11434]'",
    "HPC_SSH_ACCESS_ENABLED": "false",
    "HPC_SSH_PUBLIC_HOST": "",
}


def environment_value(value):
    """systemdの環境ファイルへ安全に書ける値へ整形する。

    Args:
        value: inventoryで指定した文字列・真偽値・数値・一覧。

    Returns:
        環境ファイルへ書く文字列。

    Raises:
        ValueError: 改行を含む設定値の場合。
    """
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, list):
        return "'" + json.dumps(value) + "'"
    value = str(value)
    if "\n" in value or "\r" in value:
        raise ValueError("任意機能の設定値に改行を含めないでください")
    return value


def feature_state(requested, missing):
    """未指定・明示的な無効化・設定不足・反映可能を判定する。

    Args:
        requested: inventoryの真偽値。未指定の場合はNone。
        missing: 不足している項目名の一覧。

    Returns:
        判定状態と不足項目。

    Raises:
        ValueError: 真偽値以外を指定した場合。
    """
    if requested is not None and type(requested) is not bool:
        raise ValueError("機能フラグはtrue・false・nullのいずれかにしてください")
    state = (
        "unspecified"
        if requested is None
        else "disabled"
        if not requested
        else "skipped"
        if missing
        else "configured"
    )
    return {"state": state, "missing": missing if state == "skipped" else []}


def optional_feature_configuration(previous, inventory):
    """既存の環境設定とinventoryから、任意機能の反映値を決める。

    Args:
        previous: 導入済みのJupyterHub環境ファイル。未導入なら空文字列。
        inventory: 任意機能のフラグ・接続先・秘密情報の辞書。

    Returns:
        環境設定、機能別の状態、不足項目。

    Raises:
        ValueError: 機能フラグや設定値の形式が不正な場合。
    """
    existing = {}
    for line in previous.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in DEFAULTS:
            existing[key] = value
    env = DEFAULTS | existing
    account = inventory.get("hpc_cloudflare_account_id") or ""
    token = inventory.get("hpc_cloudflare_api_token") or ""
    hostname = inventory.get("hpc_ssh_public_host") or ""
    issuer = (inventory.get("hpc_external_api_access_issuer") or "").rstrip("/")
    common_missing = []
    if not re.fullmatch(r"[0-9a-f]{32}", account):
        common_missing.append("hpc_cloudflare_account_id")
    if not token:
        common_missing.append("hpc_cloudflare_api_token")
    ssh_missing = list(common_missing)
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", hostname):
        ssh_missing.append("hpc_ssh_public_host")
    api_missing = list(common_missing)
    if not re.fullmatch(r"https://[a-z0-9-]+\.cloudflareaccess\.com", issuer):
        api_missing.append("hpc_external_api_access_issuer")

    states = {
        "external_api": feature_state(
            inventory.get("hpc_external_api_enabled"), api_missing
        ),
        "ssh": feature_state(inventory.get("hpc_ssh_access_enabled"), ssh_missing),
    }
    for name, env_key in (
        ("external_api", "HPC_EXTERNAL_API_ENABLED"),
        ("ssh", "HPC_SSH_ACCESS_ENABLED"),
    ):
        state = states[name]["state"]
        if state in {"configured", "disabled"}:
            env[env_key] = str(state == "configured").lower()

    if states["external_api"]["state"] == "configured":
        env["HPC_EXTERNAL_API_ACCESS_ISSUER"] = issuer
        for suffix in (
            "STATE_DIR",
            "TOKEN_LIMIT",
            "PORT_START",
            "PORT_END",
            "RESERVED_PORTS",
        ):
            key = f"hpc_external_api_{suffix.lower()}"
            if inventory.get(key) is not None:
                env[f"HPC_EXTERNAL_API_{suffix}"] = environment_value(inventory[key])
    if states["ssh"]["state"] == "configured":
        env["HPC_SSH_PUBLIC_HOST"] = hostname
    if not common_missing and any(
        row["state"] == "configured" for row in states.values()
    ):
        env["HPC_CLOUDFLARE_ACCOUNT_ID"] = environment_value(account)
        env["HPC_CLOUDFLARE_API_TOKEN"] = environment_value(token)

    return {
        "env": env,
        "states": states,
    }


class FilterModule:
    def filters(self):
        """Ansibleへ任意機能の設定判定を公開する。

        Returns:
            フィルター名と処理の対応。
        """
        return {"hpc_optional_features": optional_feature_configuration}
