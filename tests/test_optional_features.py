"""任意機能のデプロイが設定不足で既存機能を止めないことを確認する。"""

from ansible.roles.optional_features.filter_plugins.optional_features import (
    optional_feature_configuration,
)

PREVIOUS = """HPC_EXTERNAL_API_ENABLED=true
HPC_API_SERVICE_TOKEN_ENABLED=false
HPC_CLOUDFLARE_ACCOUNT_ID=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
HPC_CLOUDFLARE_API_TOKEN=existing-secret
HPC_EXTERNAL_API_ACCESS_ISSUER=https://team.cloudflareaccess.com
HPC_SSH_ACCESS_ENABLED=true
HPC_SSH_PUBLIC_HOST=ssh.example.com
"""


def test_missing_or_unspecified_configuration_preserves_existing_features():
    """未指定・設定不足は既存機能と秘密値を維持し、未導入なら無効にする。"""
    for requested in (
        {},
        {
            "hpc_ssh_access_enabled": True,
            "hpc_external_api_enabled": True,
        },
    ):
        config = optional_feature_configuration(PREVIOUS, requested)
        assert config["env"]["HPC_SSH_ACCESS_ENABLED"] == "true"
        assert config["env"]["HPC_EXTERNAL_API_ENABLED"] == "true"
        assert "HPC_API_SERVICE_TOKEN_ENABLED" not in config["env"]
        assert config["env"]["HPC_CLOUDFLARE_API_TOKEN"] == "existing-secret"
        assert config["env"]["HPC_SSH_PUBLIC_HOST"] == "ssh.example.com"
        assert config["states"]["ssh"]["state"] != "configured"
    fresh = optional_feature_configuration("", {})
    assert fresh["env"]["HPC_SSH_ACCESS_ENABLED"] == "false"
    assert fresh["env"]["HPC_EXTERNAL_API_ENABLED"] == "false"


def test_explicit_false_disables_features_without_erasing_credentials():
    """falseでAPIとSSHを停止しても、管理資格情報とSSH接続先を維持する。"""
    config = optional_feature_configuration(
        PREVIOUS,
        {"hpc_ssh_access_enabled": False, "hpc_external_api_enabled": False},
    )
    env = config["env"]
    assert env["HPC_SSH_ACCESS_ENABLED"] == "false"
    assert env["HPC_EXTERNAL_API_ENABLED"] == "false"
    assert env["HPC_CLOUDFLARE_API_TOKEN"] == "existing-secret"
    assert config["states"]["ssh"]["state"] == "disabled"
    assert config["states"]["external_api"]["state"] == "disabled"


def test_complete_configuration_enables_api_and_ssh():
    """共通資格情報と各接続先が揃えば、APIとSSHを有効にできる。"""
    config = optional_feature_configuration(
        "",
        {
            "hpc_ssh_access_enabled": True,
            "hpc_external_api_enabled": True,
            "hpc_cloudflare_account_id": "a" * 32,
            "hpc_cloudflare_api_token": "new-secret",
            "hpc_ssh_public_host": "ssh.example.com",
            "hpc_external_api_access_issuer": "https://team.cloudflareaccess.com",
        },
    )
    assert config["states"]["ssh"]["state"] == "configured"
    assert "configure_ssh" not in config
    assert config["env"]["HPC_SSH_ACCESS_ENABLED"] == "true"
    assert config["states"]["external_api"]["state"] == "configured"
    assert config["env"]["HPC_EXTERNAL_API_ENABLED"] == "true"


def test_api_enabled_without_management_credentials_preserves_previous_state():
    """API有効化に必要な管理資格情報が不足した場合は既存設定を維持する。"""
    config = optional_feature_configuration(
        PREVIOUS,
        {
            "hpc_external_api_enabled": True,
            "hpc_external_api_access_issuer": "https://team.cloudflareaccess.com",
        },
    )
    assert config["states"]["external_api"] == {
        "state": "skipped",
        "missing": ["hpc_cloudflare_account_id", "hpc_cloudflare_api_token"],
    }
    assert config["env"]["HPC_EXTERNAL_API_ENABLED"] == "true"
