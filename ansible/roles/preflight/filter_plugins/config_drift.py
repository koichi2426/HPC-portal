"""デプロイ前に、HPCで稼働中の設定と環境を表す値が一致するか確かめる。"""

import base64
import json
import re
from urllib.parse import unquote, urlsplit


def env_values(text):
    """KEY=VALUE形式の設定を、前後の引用符を除いた辞書にする。

    Args:
        text: 環境変数ファイルの内容。

    Returns:
        キーと値の辞書。
    """
    values = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator and re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            values[key] = value.strip().strip("'\"")
    return values


def deployed_values(contents):
    """各設定ファイルから、照合に使う値だけを取り出す。

    Args:
        contents: 設定ファイル名と内容の辞書。読めなかったファイルは含まない。

    Returns:
        ローカルの変数名と稼働中の値の辞書。取り出せない値はNone。
    """
    litellm = env_values(contents.get("litellm", ""))
    jupyterhub = env_values(contents.get("jupyterhub", ""))
    search_mcp = env_values(contents.get("search_mcp", ""))
    token = re.search(r"--token\s+(\S+)", contents.get("cloudflared", ""))
    searxng = re.search(
        r"^\s*secret_key:\s*(.+?)\s*$", contents.get("searxng", ""), re.MULTILINE
    )
    database = litellm.get("DATABASE_URL")
    admins = jupyterhub.get("HPC_PORTAL_ADMIN_USERS")

    try:
        searxng_secret = json.loads(searxng.group(1)) if searxng else None
    except ValueError:
        searxng_secret = None
    try:
        admin_users = json.loads(admins) if admins else None
    except ValueError:
        admin_users = None

    return {
        "litellm_master_key": litellm.get("LITELLM_MASTER_KEY"),
        "litellm_salt_key": litellm.get("LITELLM_SALT_KEY"),
        "litellm_ui_username": litellm.get("UI_USERNAME"),
        "litellm_ui_password": litellm.get("UI_PASSWORD"),
        "litellm_database_password": unquote(urlsplit(database).password or "")
        if database
        else None,
        "cloudflared_token": token.group(1) if token else None,
        "searxng_secret_key": searxng_secret,
        "search_mcp_auth_token": search_mcp.get("SEARCH_MCP_AUTH_TOKEN"),
        "hpc_cloudflare_account_id": jupyterhub.get("HPC_CLOUDFLARE_ACCOUNT_ID"),
        "hpc_cloudflare_api_token": jupyterhub.get("HPC_CLOUDFLARE_API_TOKEN"),
        "jupyterhub_public_domain": jupyterhub.get("HPC_PUBLIC_DOMAIN"),
        "jupyterhub_job_dns_domain": jupyterhub.get("HPC_JOB_DNS_DOMAIN"),
        "hpc_llm_public_base_url": jupyterhub.get("HPC_LITELLM_PUBLIC_BASE_URL"),
        "hpc_llm_admin_url": jupyterhub.get("HPC_LITELLM_ADMIN_URL"),
        "hpc_portal_admin_users": admin_users,
    }


def hpc_config_drift(results, expected):
    """稼働中の値とローカルの値が異なる項目名を返す。値そのものは返さない。

    Args:
        results: 設定ファイルを読み取ったslurpのループ結果。
        expected: ローカルの変数名と値の辞書。

    Returns:
        異なる値の変数名の一覧。片方が空・未導入の値は照合しない。
    """
    contents = {
        result["item"]["key"]: base64.b64decode(result["content"]).decode()
        for result in results
        if result.get("content")
    }
    deployed = deployed_values(contents)

    drift = []
    for name, local in expected.items():
        current = deployed.get(name)
        if current in (None, "", []) or local in (None, "", []):
            continue
        if isinstance(current, str):
            local = str(local)
        if current != local:
            drift.append(name)
    return sorted(drift)


class FilterModule:
    def filters(self):
        """Ansibleへ照合用のフィルターを登録する。

        Returns:
            フィルター名と関数の辞書。
        """
        return {"hpc_config_drift": hpc_config_drift}
