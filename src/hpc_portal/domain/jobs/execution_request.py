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
        error = policy.requested_resources_error(self.cpus, self.memory, available)
        if error:
            raise UseCaseError(error)

    @staticmethod
    def require_openwebui_slot(username: str, another_active: bool) -> None:
        if not username:
            raise ValueError("Open WebUI 用のユーザー情報を取得できません")
        if another_active:
            raise ValueError(
                "Open WebUIはユーザーごとに1つだけ起動できます。起動中のOpen WebUIを停止してから再試行してください"
            )
