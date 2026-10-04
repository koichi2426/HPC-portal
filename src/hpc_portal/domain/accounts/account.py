"""アカウントの保護と、管理者自身の権限を維持するルール。"""

from dataclasses import dataclass

from hpc_portal.domain.errors import UseCaseError


@dataclass(frozen=True)
class Account:
    username: str
    protected: bool = False

    def require_named(self) -> None:
        if not self.username:
            raise UseCaseError("username が必要です")

    def require_deletable_by(self, actor: str) -> None:
        self.require_named()
        if self.protected or self.username == actor:
            raise UseCaseError("保護されたユーザーは削除できません")

    def require_password_resettable(self) -> None:
        self.require_named()
        if self.protected:
            raise UseCaseError("保護されたユーザーは再発行できません")

    def require_sudo_changeable_by(self, actor: str, enabled: bool) -> None:
        self.require_named()
        if not enabled and self.protected:
            raise UseCaseError("保護されたユーザーのsudo権限は解除できません")
        if not enabled and self.username == actor:
            raise UseCaseError("ログイン中の自分自身のsudo権限は解除できません")

    def require_access_changeable(self) -> None:
        self.require_named()
        if self.protected:
            raise UseCaseError("保護されたユーザーのAPI利用は変更できません")
