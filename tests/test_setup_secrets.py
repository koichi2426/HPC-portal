"""秘密値の初期生成処理を検証する。"""

import re
from pathlib import Path

from scripts.setup_secrets import ensure_secrets, find_missing


TARGET_KEYS = {
    "litellm_master_key",
    "litellm_salt_key",
    "litellm_database_password",
    "searxng_secret_key",
    "search_mcp_auth_token",
}


def _read_values(path: Path) -> dict[str, str]:
    """テスト用の単純なYAMLから値を読み取る。

    Args:
        path: 読み取るファイル。

    Returns:
        キーと引用符を除いた値の辞書。
    """

    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r'^([a-z_]+):\s*"?(.*?)"?$', line)
        if match:
            values[match.group(1)] = match.group(2)
    return values


def test_ensure_secrets_generates_only_missing_or_placeholder_values(tmp_path):
    secret_file = tmp_path / "secret.yml"
    secret_file.write_text(
        'cloudflared_token: "external-token"\n'
        'litellm_master_key: "existing-master-key"\n'
        'litellm_salt_key: "REPLACE_WITH_RANDOM_SALT_KEY"\n'
        'litellm_database_password: ""\n',
        encoding="utf-8",
    )

    generated = ensure_secrets(secret_file)
    values = _read_values(secret_file)

    assert set(generated) == {
        "litellm_salt_key",
        "litellm_database_password",
        "searxng_secret_key",
        "search_mcp_auth_token",
    }
    assert values["cloudflared_token"] == "external-token"
    assert values["litellm_master_key"] == "existing-master-key"
    assert len(values["litellm_salt_key"]) == 64
    assert len(values["litellm_database_password"]) == 64
    assert len(values["searxng_secret_key"]) == 64
    assert len(values["search_mcp_auth_token"]) >= 64
    assert secret_file.stat().st_mode & 0o777 == 0o600


def test_ensure_secrets_is_idempotent(tmp_path):
    secret_file = tmp_path / "secret.yml"
    secret_file.write_text(
        "\n".join(f'{key}: "configured-{key}"' for key in TARGET_KEYS) + "\n",
        encoding="utf-8",
    )

    before = secret_file.read_bytes()
    generated = ensure_secrets(secret_file)

    assert generated == []
    assert secret_file.read_bytes() == before
    assert secret_file.stat().st_mode & 0o777 == 0o600


def test_ensure_secrets_treats_yaml_null_as_missing(tmp_path):
    secret_file = tmp_path / "secret.yml"
    secret_file.write_text(
        "litellm_master_key: null\n"
        "litellm_salt_key: ~\n"
        "litellm_database_password:\n"
        "searxng_secret_key: ''\n"
        'search_mcp_auth_token: "REPLACE_WITH_RANDOM_SEARCH_MCP_AUTH_TOKEN"\n',
        encoding="utf-8",
    )

    generated = ensure_secrets(secret_file)

    assert set(generated) == TARGET_KEYS
    assert "null" not in secret_file.read_text(encoding="utf-8")


def test_find_missing_reports_absent_and_placeholder_keys_without_writing(tmp_path):
    secret_file = tmp_path / "secret.yml"
    secret_file.write_text(
        'cloudflared_token: "external-token"\n'
        'litellm_master_key: "existing-master-key"\n'
        'litellm_salt_key: "REPLACE_WITH_RANDOM_SALT_KEY"\n'
        'litellm_database_password: ""\n'
        'searxng_secret_key: "existing-searxng-key"\n',
        encoding="utf-8",
    )
    before = secret_file.read_bytes()

    missing = find_missing(secret_file)

    assert set(missing) == {
        "litellm_salt_key",
        "litellm_database_password",
        "search_mcp_auth_token",
    }
    assert secret_file.read_bytes() == before


def test_find_missing_returns_empty_when_all_keys_configured(tmp_path):
    secret_file = tmp_path / "secret.yml"
    secret_file.write_text(
        "\n".join(f'{key}: "configured-{key}"' for key in TARGET_KEYS) + "\n",
        encoding="utf-8",
    )

    assert find_missing(secret_file) == []


def test_ensure_secrets_updates_symlink_target_without_replacing_link(tmp_path):
    target = tmp_path / "stored-secret.yml"
    target.write_text('litellm_master_key: ""\n', encoding="utf-8")
    link = tmp_path / "secret.yml"
    link.symlink_to(target)

    generated = ensure_secrets(link)

    assert set(generated) == TARGET_KEYS
    assert link.is_symlink()
    assert "REPLACE_WITH_" not in target.read_text(encoding="utf-8")
