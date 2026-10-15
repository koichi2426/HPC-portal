"""アカウントの保護と、管理者自身の権限を維持するルール。"""

from dataclasses import dataclass

from hpc_portal.domain.errors import UseCaseError


@dataclass(frozen=True)
class Account:
    username: str
    protected: bool = False

    def require_named(self) -> None:
        """操作対象のユーザー名が指定されていることを確認する。

        Raises:
            UseCaseError: ユーザー名が未指定の場合。
        """
        if not self.username:
            raise UseCaseError("username が必要です")

    def require_deletable_by(self, actor: str) -> None:
        """保護対象や操作した管理者自身の削除を拒否する。

        Args:
            actor: 操作したユーザーのLinuxユーザー名。

        Raises:
            UseCaseError: 名前が未指定、保護対象、または管理者自身を削除する場合。
        """
        self.require_named()
        if self.protected or self.username == actor:
            raise UseCaseError("保護されたユーザーは削除できません")

    def require_password_resettable(self) -> None:
        """保護対象のパスワード再発行を拒否する。

        Raises:
            UseCaseError: 名前が未指定、または保護対象のパスワードを変更する場合。
        """
        self.require_named()
        if self.protected:
            raise UseCaseError("保護されたユーザーは再発行できません")

    def require_sudo_changeable_by(self, actor: str, enabled: bool) -> None:
        """保護対象と操作した管理者自身のsudo権限を維持する。

        Args:
            actor: 操作したユーザーのLinuxユーザー名。
            enabled: sudo権限を付与する場合はTrue、解除する場合はFalse。

        Raises:
            UseCaseError: 名前が未指定、または保護対象・管理者自身のsudo権限を解除する場合。
        """
        self.require_named()
        if not enabled and self.protected:
            raise UseCaseError("保護されたユーザーのsudo権限は解除できません")
        if not enabled and self.username == actor:
            raise UseCaseError("ログイン中の自分自身のsudo権限は解除できません")

    def require_access_changeable(self) -> None:
        """保護対象のAPI利用権限の変更を拒否する。

        Raises:
            UseCaseError: 名前が未指定、または保護対象のAPI利用を変更する場合。
        """
        self.require_named()
        if self.protected:
            raise UseCaseError("保護されたユーザーのAPI利用は変更できません")
