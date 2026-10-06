"""操作失敗を画面やHTTPに依存せず伝える。"""


class UseCaseError(ValueError):
    def __init__(self, message: str, code: str = "invalid"):
        """利用者向けの説明と、呼び出し側が判断するエラー種別を保持する。

        Args:
            message: 利用者へ返す説明またはエラーメッセージ。
            code: 呼び出し側がエラー種別を判断するための識別子。
        """
        super().__init__(message)
        self.code = code
