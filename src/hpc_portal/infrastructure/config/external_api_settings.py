"""単一ホストでの外部API公開設定。有効化時だけ専用設定を検証する。"""

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExternalApiSettings:
    enabled: bool = False
    state_dir: str = "/var/lib/jupyterhub/external-api-v2"
    account_id: str = ""
    api_token: str = ""
    public_host: str = ""
    access_issuer: str = ""
    token_limit: int = 50
    min_uid: int = 1000
    port_start: int = 20000
    port_end: int = 29999
    reserved_ports: tuple[int, ...] = (
        22,
        80,
        443,
        4000,
        5432,
        8000,
        8001,
        8081,
        8888,
        8890,
        11434,
    )
    candidate_count: int = 24
    max_apps: int = 50
    body_limit: int = 64 * 1024 * 1024
    timeout: int = 60
    concurrency: int = 16

    @classmethod
    def from_env(cls):
        """外部API公開に使う環境変数を、既定値付きで読み込む。

        Returns:
            外部APIの接続・保存先・ポート範囲の設定。

        Raises:
            ValueError: 整数や予約ポート一覧の環境変数を解釈できない場合。
        """
        return cls(
            enabled=os.environ.get("HPC_EXTERNAL_API_ENABLED", "false").lower()
            == "true",
            state_dir=os.environ.get("HPC_EXTERNAL_API_STATE_DIR", cls.state_dir),
            account_id=os.environ.get("HPC_CLOUDFLARE_ACCOUNT_ID", ""),
            api_token=os.environ.get("HPC_CLOUDFLARE_API_TOKEN", ""),
            public_host=os.environ.get("HPC_PUBLIC_DOMAIN", ""),
            access_issuer=os.environ.get("HPC_EXTERNAL_API_ACCESS_ISSUER", "").rstrip(
                "/"
            ),
            token_limit=int(os.environ.get("HPC_EXTERNAL_API_TOKEN_LIMIT", "50")),
            min_uid=int(os.environ.get("HPC_PORTAL_USER_MIN_UID", "1000")),
            port_start=int(os.environ.get("HPC_EXTERNAL_API_PORT_START", "20000")),
            port_end=int(os.environ.get("HPC_EXTERNAL_API_PORT_END", "29999")),
            reserved_ports=tuple(
                json.loads(
                    os.environ.get(
                        "HPC_EXTERNAL_API_RESERVED_PORTS",
                        json.dumps(cls.reserved_ports),
                    )
                )
            ),
        )

    def validate(self):
        """有効化された外部APIの管理設定と保存先・ポート範囲を検証する。

        Raises:
            ValueError: 機能が無効、必要な設定が不足、または設定形式が不正な場合。
        """
        if not self.enabled:
            raise ValueError("外部 API 公開は管理者が有効化していません")
        if not re.fullmatch(r"[0-9a-f]{32}", self.account_id) or not self.api_token:
            raise ValueError("Cloudflare の管理設定が不足しています")
        if not re.fullmatch(
            r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", self.public_host
        ):
            raise ValueError("公開ドメインが不正です")
        if not re.fullmatch(
            r"https://[a-z0-9-]+\.cloudflareaccess\.com", self.access_issuer
        ):
            raise ValueError("Cloudflare Access のチーム URL が必要です")
        if not Path(self.state_dir).is_absolute() or not (
            1024 <= self.port_start <= self.port_end <= 65535
        ):
            raise ValueError("API 公開の保管先またはポート範囲が不正です")
        if any(
            type(port) is not int or not 1 <= port <= 65535
            for port in self.reserved_ports
        ):
            raise ValueError("予約ポート設定が不正です")
