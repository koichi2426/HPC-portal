"""操作失敗を画面やHTTPに依存せず伝える。"""


class UseCaseError(ValueError):
    def __init__(self, message: str, code: str = "invalid"):
        super().__init__(message)
        self.code = code
