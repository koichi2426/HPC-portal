"""LiteLLM管理APIへのHTTP接続。"""

import json
import urllib.error
import urllib.request


class LiteLlmClient:
    def __init__(self, base_url: str, master_key: str):
        self.base_url = base_url
        self.master_key = master_key

    def enabled(self) -> bool:
        """LiteLLM管理APIを利用可能か判定する。

        Returns:
            内部URLと管理キーが設定済みならTrue。
        """
        return bool(self.base_url and self.master_key)

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
        if not self.enabled():
            raise RuntimeError("LiteLLM Admin API が未設定です")
        url = self.base_url + path
        data = None
        headers = {
            "Authorization": f"Bearer {self.master_key}",
            "Accept": "application/json",
        }
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"LiteLLM API HTTP {exc.code}: {body[:300]}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"LiteLLM API 接続失敗: {exc.reason}") from exc
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw}
