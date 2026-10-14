"""统一错误信封。前端只按这一种结构解析错误。

不需要数据库：这里自己搭一个带同样 exception handler 的小 FastAPI 应用。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import (
    AppError,
    ConversationNotFound,
    LLMRateLimitError,
    register_exception_handlers,
)

SECRET = "sk-super-secret-value"


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/not-found")
    async def not_found() -> dict:
        raise ConversationNotFound()

    @app.get("/rate-limited")
    async def rate_limited() -> dict:
        raise LLMRateLimitError()

    @app.get("/needs-param")
    async def needs_param(required: int) -> dict:
        return {"required": required}

    @app.get("/custom-status")
    async def custom_status() -> dict:
        raise AppError("温度参数超范围", status_code=400)

    @app.get("/crash")
    async def crash() -> dict:
        raise RuntimeError(f"内部细节不该外泄 {SECRET}")

    # raise_server_exceptions=False：让注册的兜底 handler 真的执行
    return TestClient(app, raise_server_exceptions=False)


def assert_envelope_shape(body: dict) -> None:
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "request_id"}
    assert isinstance(body["error"]["code"], str)
    assert isinstance(body["error"]["message"], str)
    assert isinstance(body["error"]["request_id"], str)


def test_app_error_uses_its_status_code_and_code(client: TestClient):
    response = client.get("/not-found")
    assert response.status_code == 404
    body = response.json()
    assert_envelope_shape(body)
    assert body["error"]["code"] == "conversation_not_found"


def test_rate_limit_maps_to_429(client: TestClient):
    response = client.get("/rate-limited")
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "llm_rate_limited"


def test_custom_status_code_override(client: TestClient):
    response = client.get("/custom-status")
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "温度参数超范围"


def test_request_validation_error_uses_envelope(client: TestClient):
    response = client.get("/needs-param")
    assert response.status_code == 422
    body = response.json()
    assert_envelope_shape(body)
    assert body["error"]["code"] == "validation_error"
    # 提示里要带上出错的字段名，否则前端不知道该改什么
    assert "required" in body["error"]["message"]


def test_unknown_route_also_uses_envelope(client: TestClient):
    response = client.get("/definitely-not-a-route")
    assert response.status_code == 404
    assert_envelope_shape(response.json())


def test_unhandled_exception_hides_internals(client: TestClient):
    response = client.get("/crash")
    assert response.status_code == 500
    body = response.json()
    assert_envelope_shape(body)
    assert body["error"]["code"] == "internal_error"
    # 关键：密钥和堆栈都不能出现在响应体里
    assert SECRET not in response.text
    assert "RuntimeError" not in response.text
    assert "Traceback" not in response.text
