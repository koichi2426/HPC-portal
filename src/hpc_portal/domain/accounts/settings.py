"""ユーザー管理で使う権限と接続先の設定。"""

from dataclasses import dataclass


@dataclass
class UserManagementSettings:
    grant_sudo: bool
    protected_users: frozenset[str]
    llm_public_base_url: str
