"""LLM利用者と、キーの用途・所有者に関するルール。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class LlmAccess:
    username: str
    disabled: bool = False

    def issuance_error(self, message: str) -> str | None:
        return message if self.disabled else None


@dataclass(frozen=True)
class LlmKey:
    owner_ids: frozenset[str]
    alias: str
    source: str

    def belongs_to(self, username: str) -> bool:
        return username in self.owner_ids

    def is_external_key_for(self, username: str) -> bool:
        # 外部API用とOpen WebUI用のキーは、用途ごとのaliasで区別する。
        return self.alias == username

    @property
    def is_openwebui(self) -> bool:
        return self.source == "hpc-portal-openwebui"
