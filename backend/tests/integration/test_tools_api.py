"""只读工具清单 API。"""

from __future__ import annotations

from httpx import AsyncClient

from app.core.config import get_settings
from app.search import search_available


async def test_list_tools_returns_openai_function_shapes(client: AsyncClient) -> None:
    response = await client.get("/api/v1/tools")
    assert response.status_code == 200, response.text

    tools = {item["name"]: item for item in response.json()["tools"]}
    assert {"calculator", "document_search", "web_search"} <= set(tools)

    calculator = tools["calculator"]
    assert calculator["available"] is True
    assert calculator["timeout_seconds"] > 0
    assert calculator["parameters"]["type"] == "object"
    assert calculator["parameters"]["additionalProperties"] is False
    assert "expression" in calculator["parameters"]["properties"]


async def test_web_search_availability_matches_configuration(client: AsyncClient) -> None:
    response = await client.get("/api/v1/tools")
    assert response.status_code == 200
    tools = {item["name"]: item for item in response.json()["tools"]}

    expected = search_available(get_settings())
    assert tools["web_search"]["available"] is expected
    if not expected:
        assert tools["web_search"]["unavailable_reason"]


async def test_tool_list_does_not_execute_anything(client: AsyncClient) -> None:
    # 调用两次结果一致，说明没有副作用/不依赖执行状态。
    first = await client.get("/api/v1/tools")
    second = await client.get("/api/v1/tools")
    assert first.json() == second.json()
