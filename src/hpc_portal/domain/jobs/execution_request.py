"""アプリ起動要求の実行条件。"""

from dataclasses import dataclass

from hpc_portal.domain.errors import UseCaseError


@dataclass(frozen=True)
class ExecutionRequest:
    application: str
    cpus: str
    memory: str
    runtime: str
    use_gpu: bool

    def require_resources(self, policy, available: dict | None) -> None:
        """要求したCPUとメモリが割当可能量に収まるか確認する。

        Args:
            policy: 入力値・リソース・実行条件を判断するルール。
            available: 現在の割当可能リソース。取得できない場合はNone。

        Raises:
            UseCaseError: 要求するCPU・メモリが割当可能量を超える場合。
        """
        error = policy.requested_resources_error(self.cpus, self.memory, available)
        if error:
            raise UseCaseError(error)

    @staticmethod
    def require_openwebui_slot(username: str, another_active: bool) -> None:
        """ユーザー名を確認し、Open WebUIの同時起動を拒否する。

        Args:
            username: 対象のLinuxユーザー名。
            another_active: 同じユーザーのOpen WebUIが既に稼働しているか。

        Raises:
            ValueError: ユーザー名を取得できない、または同じユーザーが既にOpen WebUIを起動している場合。
        """
        if not username:
            raise ValueError("Open WebUI 用のユーザー情報を取得できません")
        if another_active:
            raise ValueError(
                "Open WebUIはユーザーごとに1つだけ起動できます。起動中のOpen WebUIを停止してから再試行してください"
            )
