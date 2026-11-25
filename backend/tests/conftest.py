"""测试全局配置。

**必须在任何 app 模块被 import 之前跑完**：app.core.config.get_settings 是
lru_cache 的，一旦被调用配置就固化了。pytest 会先加载 rootdir 下的 conftest，
早于所有测试模块，所以这里是唯一可靠的注入点。

测试库地址从 backend/.env 的 TEST_DATABASE_URL 读（该文件不进版本控制）。
没有配置就整体跳过集成测试，绝不回落到开发库——那些用例会清空数据。
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile

import pytest

# 上传目录必须在 get_settings() 首次调用前注入，否则 lru_cache 会固化掉
# 真实的 UPLOAD_DIR，测试就会往开发目录写文件。
_UPLOAD_ROOT = tempfile.mkdtemp(prefix="ai-assistant-uploads-")
os.environ["UPLOAD_DIR"] = _UPLOAD_ROOT
atexit.register(shutil.rmtree, _UPLOAD_ROOT, ignore_errors=True)

from app.core.config import get_settings  # noqa: E402

_settings = get_settings()
_test_database_url = _settings.test_database_url

if _test_database_url:
    os.environ["DATABASE_URL"] = _test_database_url
    # 测试绝不调用真实模型：固定用 mock，并关掉模拟延时。
    os.environ["LLM_PROVIDER"] = "mock"
    os.environ["MOCK_STREAM_DELAY_MS"] = "0"
    # 测试也不发真实联网请求；需要搜索时用 dependency_overrides 注入 fake。
    os.environ["SEARCH_PROVIDER"] = "none"
    os.environ["SEARCH_FALLBACK_PROVIDER"] = ""
    # 测试默认不调用额外 LLM 做查询规划。
    os.environ["SEARCH_LLM_PLANNING_ENABLED"] = "false"
    # P2 工具/Agent 的确定性配置：不依赖开发者 .env。
    os.environ["TOOLS_ENABLED"] = "calculator,document_search,web_search"
    os.environ["AGENT_MAX_TOOL_CALLS"] = "3"
    os.environ["AGENT_TOTAL_TIMEOUT_SECONDS"] = "90"
    os.environ["TOOL_TIMEOUT_SECONDS"] = "15"
    os.environ["SEARCH_TOTAL_TIMEOUT_SECONDS"] = "12"
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
