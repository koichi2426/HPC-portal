"""LLM利用者と、キーの用途・所有者に関するルール。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class LlmAccess:
    username: str
    disabled: bool = False

    def issuance_error(self, message: str) -> str | None:
        """管理者が利用停止にしたユーザーへのキー発行を拒否する。

        Args:
            message: 利用者へ返す説明またはエラーメッセージ。

        Returns:
            停止中なら指定された説明、利用可能ならNone。
        """
        return message if self.disabled else None


@dataclass(frozen=True)
class LlmKey:
    owner_ids: frozenset[str]
    alias: str
    source: str

    def belongs_to(self, username: str) -> bool:
        """キーの所有者情報に対象ユーザーが含まれるか確認する。

        Args:
            username: 対象のLinuxユーザー名。

        Returns:
            所有者が一致する場合はTrue。
        """
        return username in self.owner_ids

    def is_external_key_for(self, username: str) -> bool:
        # 外部API用とOpen WebUI用のキーは、用途ごとのaliasで区別する。
        """キーのaliasが対象ユーザーのLLM API用を示すか確認する。

        Args:
            username: 対象のLinuxユーザー名。

        Returns:
            aliasがユーザー名と一致する場合はTrue。
        """
        return self.alias == username

    @property
    def is_openwebui(self) -> bool:
        """キーがポータル発行のOpen WebUI専用か確認する。

        Returns:
            専用のsource識別子が一致する場合はTrue。
        """
        return self.source == "hpc-portal-openwebui"
