"""Web検索MCPの内部Bearer tokenを検証する。"""

from __future__ import annotations

import hmac


def validate_bearer_token(candidate: str, expected: str) -> bool:
    """Bearer tokenを処理時間の差が出にくい方法で比較する。

    Args:
        candidate: MCPクライアントから受け取ったtoken。
        expected: サーバーへ安全に設定された正しいtoken。

    Returns:
        tokenが一致し、必要な長さを満たす場合はTrue。
    """
    if len(expected) < 32 or not candidate:
        return False
    return hmac.compare_digest(candidate.encode(), expected.encode())
