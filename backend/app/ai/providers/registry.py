"""按配置选择 LLM provider。

服务层和 endpoint 通过依赖注入拿 provider，不直接构造，也不 import 具体实现。
"""

from __future__ import annotations

from functools import lru_cache

from app.ai.providers.base import LLMProvider
from app.ai.providers.mock import MockProvider
from app.ai.providers.openai_compat import OpenAICompatibleProvider
from app.core.config import get_settings


@lru_cache
def get_llm_provider() -> LLMProvider:
    settings = get_settings()

    if settings.llm_provider == "mock":
        return MockProvider(
            chunk_size=settings.mock_chunk_size,
            stream_delay_ms=settings.mock_stream_delay_ms,
            reply_template=settings.mock_default_reply,
            force_error=settings.mock_force_error,
        )

    if settings.llm_provider == "deepseek":
        # 缺 key / 缺 model 的情况在 Settings 校验里就已经拦下，这里不会拿到空值。
        return OpenAICompatibleProvider(
            name="deepseek",
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value(),
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
            default_max_output_tokens=settings.llm_max_output_tokens,
        )

    raise ValueError(f"未实现的 LLM_PROVIDER：{settings.llm_provider!r}")


def llm_provider_dep() -> LLMProvider:
    """FastAPI 依赖：overrides 里替换它即可在测试中注入 MockProvider。"""
    return get_llm_provider()
