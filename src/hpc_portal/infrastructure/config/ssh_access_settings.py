"""HTTP APIとは独立して有効化するSSH公開設定。"""

import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SshAccessSettings:
    enabled: bool = False
    public_host: str = ""
    account_id: str = ""
    api_token: str = ""
    state_dir: str = "/var/lib/jupyterhub/external-api-v2"
    token_limit: int = 50

    @classmethod
    def from_env(cls):
        """SSH公開と共有Cloudflare接続の環境変数を読む。

        Returns:
            SSH機能・公開ホスト・保存先の設定。
        """
        return cls(
            enabled=os.environ.get("HPC_SSH_ACCESS_ENABLED", "false").lower() == "true",
            public_host=os.environ.get("HPC_SSH_PUBLIC_HOST", ""),
            account_id=os.environ.get("HPC_CLOUDFLARE_ACCOUNT_ID", ""),
            api_token=os.environ.get("HPC_CLOUDFLARE_API_TOKEN", ""),
            state_dir=os.environ.get("HPC_EXTERNAL_API_STATE_DIR", cls.state_dir),
            token_limit=int(os.environ.get("HPC_EXTERNAL_API_TOKEN_LIMIT", "50")),
        )

    def validate(self):
        """SSH有効時に、公開ホストと管理資格情報を検証する。

        Raises:
            ValueError: 必要な管理設定が不足、または形式が不正な場合。
        """
        if not self.enabled:
            raise ValueError("SSH公開は管理者が有効化していません")
        if not re.fullmatch(r"[0-9a-f]{32}", self.account_id) or not self.api_token:
            raise ValueError("Cloudflareの管理設定が不足しています")
        if not re.fullmatch(
            r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", self.public_host
        ):
            raise ValueError("SSH公開ホストが不正です")
        if not Path(self.state_dir).is_absolute() or self.token_limit < 1:
            raise ValueError("保存先またはトークン上限が不正です")
