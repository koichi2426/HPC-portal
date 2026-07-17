"""SearXNG検索をLiteLLMへ公開する内部MCPサーバー。"""

from __future__ import annotations

import os
from typing import Any

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from search_service import DEFAULT_RESULT_COUNT, search_web as run_search


mcp = FastMCP("HPC Web Search")


@mcp.custom_route("/health", methods=["GET"])
async def health_check(_request: Request) -> JSONResponse:
    """サービスの稼働状態を返す。

    Args:
        _request: FastMCPから渡されるHTTPリクエスト。

    Returns:
        稼働中であることを示すJSONレスポンス。
    """
    return JSONResponse({"status": "ok", "service": "hpc-search-mcp"})


@mcp.tool(name="search_web")
def search_web(
    query: str,
    count: int = DEFAULT_RESULT_COUNT,
    language: str = "ja-JP",
) -> dict[str, Any]:
    """Webを検索して、関連ページのタイトル・URL・概要を返す。

    Args:
        query: 調べたい内容を表す検索語。
        count: 返してほしい検索結果数。
        language: 検索言語。通常はja-JPを使用する。

    Returns:
        SearXNGから取得した検索結果。
    """
    return run_search(query=query, count=count, language=language)


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host=os.environ.get("SEARCH_MCP_HOST", "127.0.0.1"),
        port=int(os.environ.get("SEARCH_MCP_PORT", "8890")),
        path="/mcp",
        stateless_http=True,
    )
