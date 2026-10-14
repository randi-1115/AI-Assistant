"""测试全局配置。

**必须在任何 app 模块被 import 之前跑完**：app.core.config.get_settings 是
lru_cache 的，一旦被调用配置就固化了。pytest 会先加载 rootdir 下的 conftest，
早于所有测试模块，所以这里是唯一可靠的注入点。

测试库地址从 backend/.env 的 TEST_DATABASE_URL 读（该文件不进版本控制）。
没有配置就整体跳过集成测试，绝不回落到开发库——那些用例会清空数据。
"""

from __future__ import annotations

import os

import pytest

from app.core.config import get_settings

_settings = get_settings()
_test_database_url = _settings.test_database_url

if _test_database_url:
    os.environ["DATABASE_URL"] = _test_database_url
    # 测试绝不调用真实模型：固定用 mock，并关掉模拟延时。
    os.environ["LLM_PROVIDER"] = "mock"
    os.environ["MOCK_STREAM_DELAY_MS"] = "0"
    # 让后续 get_settings() 重新读取上面这些覆盖值。
    get_settings.cache_clear()

def pytest_collection_modifyitems(items) -> None:
    """没配测试库时，跳过整个 tests/integration，而不是让它去连开发库。"""
    if _test_database_url:
        return
    skip = pytest.mark.skip(reason="未配置 TEST_DATABASE_URL，跳过集成测试（见 .env.example）")
    for item in items:
        if "/integration/" in str(item.fspath).replace("\\", "/"):
            item.add_marker(skip)
