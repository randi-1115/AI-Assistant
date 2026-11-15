"""联网搜索在聊天流中的端到端行为（fake provider，不访问外网）。"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core.config import get_settings
from app.main import app
from app.search import get_search_service
from app.search.base import SearchProvider, SearchResult, SearchTimeoutError
from app.search.service import FallbackSearchProvider, SearchService
from tests.integration.conftest import create_conversation, upload_file
from tests.integration.test_chat_stream_api import names, parse_sse


class FakeProvider(SearchProvider):
    name = "fake"

    def __init__(
        self,
        *,
        name: str = "fake",
        results: list[SearchResult] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.name = name
        self.results = results or []
        self.error = error
        self.calls = 0
        self.last_time_range: str | None = None
        self.last_topic: str | None = None

    @property
    def configured(self) -> bool:
        return True

    async def search(
        self,
        query: str,
        *,
        max_results: int,
        timeout_seconds: float,
        time_range: str | None = None,
        topic: str | None = None,
    ) -> list[SearchResult]:
        self.calls += 1
        self.last_time_range = time_range
        self.last_topic = topic
        if self.error is not None:
            raise self.error
        return list(self.results)[:max_results]


def result(
    title: str = "示例来源",
    url: str = "https://example.com/a",
    snippet: str = "网页摘要",
) -> SearchResult:
    return SearchResult(title=title, url=url, snippet=snippet)


def install_service(provider: SearchProvider | None, *, resolve: bool = True) -> SearchService:
    service = SearchService(get_settings(), provider=provider, resolve_provider=resolve)
    app.dependency_overrides[get_search_service] = lambda: service
    return service


@pytest.fixture(autouse=True)
def _clear_search_override():
    yield
    app.dependency_overrides.pop(get_search_service, None)


async def stream(
    client: AsyncClient,
    conversation_id: int,
    content: str,
    *,
    search_mode: str | None = None,
    attachment_ids: list[int] | None = None,
):
    body: dict = {"content": content}
    if search_mode is not None:
        body["search_mode"] = search_mode
    if attachment_ids:
        body["attachment_ids"] = attachment_ids
    return await client.post(
        f"/api/v1/conversations/{conversation_id}/messages/stream", json=body
    )


async def messages_of(client: AsyncClient, conversation_id: int) -> list[dict]:
    response = await client.get(f"/api/v1/conversations/{conversation_id}/messages")
    assert response.status_code == 200
    return response.json()


def role_message(messages: list[dict], role: str) -> dict:
    return next(message for message in messages if message["role"] == role)


def tool_statuses(events: list[tuple[str, dict]]) -> list[dict]:
    return [data for name, data in events if name == "tool_status"]


def source_payloads(events: list[tuple[str, dict]]) -> list[dict]:
    return [data for name, data in events if name == "source"]


async def test_default_search_mode_is_off(client: AsyncClient):
    provider = FakeProvider(results=[result()])
    install_service(provider)
    conversation = await create_conversation(client)

    response = await stream(client, conversation["id"], "你好")
    events = parse_sse(response.text)

    assert provider.calls == 0
    assert "tool_status" not in names(events)
    assert source_payloads(events) == []


async def test_explicit_off_does_not_search(client: AsyncClient):
    provider = FakeProvider(results=[result()])
    install_service(provider)
    conversation = await create_conversation(client)

    response = await stream(client, conversation["id"], "你好", search_mode="off")
    events = parse_sse(response.text)

    assert provider.calls == 0
    assert "tool_status" not in names(events)


async def test_auto_success_emits_running_sources_and_done(client: AsyncClient):
    provider = FakeProvider(
        results=[
            result("来源一", "https://example.com/one", "摘要一"),
            result("来源二", "https://example.com/two", "摘要二"),
        ]
    )
    install_service(provider)
    conversation = await create_conversation(client)

    response = await stream(client, conversation["id"], "最新的消息是什么？", search_mode="auto")
    events = parse_sse(response.text)
    order = names(events)

    statuses = tool_statuses(events)
    assert statuses[0]["status"] == "running"
    assert statuses[-1]["status"] == "done"
    assert statuses[-1]["provider"] == "fake"

    web_sources = [item for item in source_payloads(events) if item["kind"] == "web"]
    assert len(web_sources) == 2
    assert web_sources[0]["title"] == "来源一"
    assert web_sources[0]["url"] == "https://example.com/one"
    assert web_sources[0]["snippet"] == "摘要一"

    assert order.index("tool_status") < order.index("source") < order.index("token")
    assert order[0] == "message_start"
    assert order[-1] == "message_end"

    messages = await messages_of(client, conversation["id"])
    user = role_message(messages, "user")
    assistant = role_message(messages, "assistant")
    assert user["metadata_json"]["search_mode"] == "auto"
    saved_sources = assistant["metadata_json"]["sources"]
    assert len([item for item in saved_sources if item["kind"] == "web"]) == 2
    saved_statuses = assistant["metadata_json"]["tool_statuses"]
    assert saved_statuses[0]["status"] == "done"
    assert all(item["status"] != "running" for item in saved_statuses)


async def test_recency_query_passes_news_filters(client: AsyncClient):
    provider = FakeProvider(results=[result("新新闻")])
    install_service(provider)
    conversation = await create_conversation(client)

    response = await stream(
        client,
        conversation["id"],
        "最近一周AI圈有什么新闻",
        search_mode="auto",
    )
    events = parse_sse(response.text)

    assert provider.last_time_range == "week"
    assert provider.last_topic == "news"
    assert tool_statuses(events)[-1]["status"] == "done"


async def test_auto_empty_results(client: AsyncClient):
    provider = FakeProvider(results=[])
    install_service(provider)
    conversation = await create_conversation(client)

    response = await stream(client, conversation["id"], "没有结果的问题", search_mode="auto")
    events = parse_sse(response.text)

    assert tool_statuses(events)[-1]["status"] == "empty"
    assert [item for item in source_payloads(events) if item["kind"] == "web"] == []
    assert "token" in names(events)
    assistant = role_message(await messages_of(client, conversation["id"]), "assistant")
    assert assistant["metadata_json"]["tool_statuses"][0]["status"] == "empty"


async def test_auto_unavailable_does_not_break_chat(client: AsyncClient):
    install_service(None, resolve=False)
    conversation = await create_conversation(client)

    response = await stream(client, conversation["id"], "今天有什么新闻？", search_mode="auto")
    events = parse_sse(response.text)

    assert tool_statuses(events)[-1]["status"] == "unavailable"
    assert [item for item in source_payloads(events) if item["kind"] == "web"] == []
    assert "token" in names(events)
    assert "message_end" in names(events)
    assistant = role_message(await messages_of(client, conversation["id"]), "assistant")
    assert assistant["status"] == "complete"
    assert assistant["metadata_json"]["tool_statuses"][0]["status"] == "unavailable"


async def test_fallback_provider_is_used(client: AsyncClient):
    primary = FakeProvider(name="primary", error=SearchTimeoutError("主源超时"))
    fallback = FakeProvider(name="fallback", results=[result("备源结果")])
    install_service(FallbackSearchProvider(primary, fallback))
    conversation = await create_conversation(client)

    response = await stream(client, conversation["id"], "问题", search_mode="auto")
    events = parse_sse(response.text)

    web_sources = [item for item in source_payloads(events) if item["kind"] == "web"]
    assert web_sources[0]["title"] == "备源结果"
    assert tool_statuses(events)[-1]["provider"] == "fallback"


async def test_file_and_web_sources_are_ordered(client: AsyncClient):
    uploaded = await upload_file(client, content="项目代号是蓝鲸。".encode())
    file_id = uploaded.json()["file_id"]
    install_service(FakeProvider(results=[result("网页来源", "https://example.com/web")]))
    conversation = await create_conversation(client)

    response = await stream(
        client,
        conversation["id"],
        "项目代号是什么？",
        search_mode="auto",
        attachment_ids=[file_id],
    )
    events = parse_sse(response.text)

    sources = source_payloads(events)
    assert sources[0]["kind"] == "file"
    assert sources[1]["kind"] == "web"
    assistant = role_message(await messages_of(client, conversation["id"]), "assistant")
    assert [item["kind"] for item in assistant["metadata_json"]["sources"]] == ["file", "web"]


async def test_regenerate_inherits_search_mode(client: AsyncClient):
    install_service(FakeProvider(results=[result("重新生成的来源")]))
    conversation = await create_conversation(client)
    await stream(client, conversation["id"], "问题", search_mode="auto")

    response = await client.post(
        f"/api/v1/conversations/{conversation['id']}/regenerate"
    )
    assert response.status_code == 200, response.text
    events = parse_sse(response.text)

    web_sources = [item for item in source_payloads(events) if item["kind"] == "web"]
    assert web_sources
    assert "tool_status" in names(events)
    assistant = role_message(await messages_of(client, conversation["id"]), "assistant")
    assert any(item["kind"] == "web" for item in assistant["metadata_json"]["sources"])


async def test_regenerate_off_message_does_not_search(client: AsyncClient):
    provider = FakeProvider(results=[result()])
    install_service(provider)
    conversation = await create_conversation(client)
    await stream(client, conversation["id"], "问题", search_mode="off")

    response = await client.post(
        f"/api/v1/conversations/{conversation['id']}/regenerate"
    )
    events = parse_sse(response.text)

    assert provider.calls == 0
    assert "tool_status" not in names(events)


async def test_unexpected_search_error_degrades(client: AsyncClient):
    install_service(FakeProvider(error=RuntimeError("boom")))
    conversation = await create_conversation(client)

    response = await stream(client, conversation["id"], "问题", search_mode="auto")
    events = parse_sse(response.text)

    assert tool_statuses(events)[-1]["status"] == "unavailable"
    assert "token" in names(events)
    assert "message_end" in names(events)
