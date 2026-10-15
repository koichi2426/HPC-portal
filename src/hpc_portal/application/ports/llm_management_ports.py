"""LLM操作に必要な外部接続の契約。"""

from typing import Protocol


class LlmClient(Protocol):
    def enabled(self) -> bool:
        """LiteLLM管理APIを利用可能か判定する。

        Returns:
            内部URLと管理キーが設定済みならTrue。
        """
        ...

    def request(
        self, path: str, payload: dict | None = None, method: str = "POST"
    ) -> dict:
        """LiteLLM管理APIへ認証付きリクエストを送る。

        Args:
            path: 内部Base URLからのAPIパス。
            payload: JSONとして送信する辞書。GETではNoneを指定する。
            method: HTTPメソッド。

        Returns:
            JSONレスポンスを辞書化した値。

        Raises:
            RuntimeError: 設定不足、HTTPエラー、通信失敗、JSON形式不正の場合。
        """
        ...


class ModelInventory(Protocol):
    def has_model(self, model: str) -> tuple[bool, str | None]:
        """指定モデルがOllamaに存在するか確認する。

        Args:
            model: 確認するOllamaモデル名。

        Returns:
            ``(存在するか, エラー)``。
        """
        ...

    def model_names(self) -> tuple[list[str], str | None]:
        """Ollamaへインストール済みのモデル名を取得する。

        Returns:
            ``(重複を除いて並べ替えたモデル名, エラー)``。
        """
        ...

    def model_supports_tools(self, model: str) -> tuple[bool | None, str | None]:
        """Ollamaモデルがツール呼び出しに対応するか確認する。

        Args:
            model: 確認するOllamaモデル名。

        Returns:
            ``(tools capabilityの有無, エラー)``。取得失敗時の値はNone。
        """
        ...


class KeyStore(Protocol):
    def read(self, username: str) -> str:
        """保存済みOpen WebUI Keyを読む。

        Args:
            username: Linuxユーザー名。

        Returns:
            Key文字列。未保存または読込失敗時は空文字列。
        """
        ...

    def write(self, username: str, key: str) -> str | None:
        """Open WebUI Keyをroot専用ファイルへ原子的に保存する。

        Args:
            username: Linuxユーザー名。
            key: 保存する平文Virtual Key。

        Returns:
            正常ならNone、失敗時はエラーメッセージ。
        """
        ...

    def remove(self, username: str) -> None:
        """保存済みOpen WebUI Keyファイルを削除する。

        Args:
            username: Linuxユーザー名。
        """
        ...
