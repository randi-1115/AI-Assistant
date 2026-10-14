"""集中式应用配置。

所有配置项都从后端环境变量或 backend/.env 读取，密钥只出现在后端。
前端环境变量（VITE_ 前缀）会打包进浏览器，绝不放服务商密钥。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> parents[2] == backend/
BACKEND_DIR = Path(__file__).resolve().parents[2]

SUPPORTED_LLM_PROVIDERS = ("mock", "deepseek")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- 应用 ---
    app_env: str = "development"
    app_name: str = "ai-assistant"
    app_version: str = "0.1.0"
    api_v1_prefix: str = "/api/v1"
    debug: bool = True
    log_level: str = "INFO"

    # --- 数据库 ---
    # 例：mysql+aiomysql://root:密码@127.0.0.1:3306/ai_assistant?charset=utf8mb4
    database_url: str
    # 集成测试用的独立库。留空则集成测试整体跳过——刻意不给默认值，
    # 避免把凭据写进会被提交的代码里。
    test_database_url: str = ""

    # --- LLM ---
    llm_provider: str = "mock"
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""
    llm_vision_model: str = ""
    llm_timeout_seconds: float = 60.0
    llm_max_output_tokens: int = 2048
    llm_temperature: float = 0.7

    # --- 上下文窗口 ---
    # 只发送最近若干条消息，并按字符预算裁剪，避免无限传送历史对话。
    context_max_messages: int = 20
    context_max_chars: int = 8000

    # --- CORS ---
    # 逗号分隔。用字符串而非 list，避免 pydantic-settings 要求 JSON 写法。
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # --- 本地开发用户 ---
    # 第一阶段单用户，不做登录认证；所有查询仍按 user_id 过滤，为后续认证留位。
    dev_user_email: str = "dev@local"
    dev_user_display_name: str = "本地开发用户"

    # --- mock provider（仅供离线开发与测试，不联网）---
    mock_stream_delay_ms: int = 60
    mock_chunk_size: int = 4
    mock_default_reply: str = (
        "这是 mock provider 的固定回答。当前 LLM_PROVIDER=mock，"
        "没有调用任何真实模型；把 .env 里的 LLM_PROVIDER 改成 deepseek 并填好 "
        "LLM_API_KEY 之后，才会走真实模型。"
    )
    # 全局强制触发错误，用于手工验收错误分支：upstream|timeout|rate_limit|auth
    mock_force_error: str = ""

    # --- 后续阶段占位（P0 未使用，保留键名以匹配 .env.example）---
    search_provider: str = "none"
    search_api_key: SecretStr = SecretStr("")
    upload_dir: str = "../data/uploads"
    max_upload_mb: int = 20
    rag_top_k: int = 4
    agent_max_tool_calls: int = 3

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @model_validator(mode="after")
    def _check_llm_config(self) -> "Settings":
        if self.llm_provider not in SUPPORTED_LLM_PROVIDERS:
            raise ValueError(
                f"LLM_PROVIDER 取值非法：{self.llm_provider!r}，"
                f"只支持 {SUPPORTED_LLM_PROVIDERS}"
            )
        if self.llm_provider == "deepseek":
            # 这里刻意快速失败并给出明确提示，而不是等到第一次聊天才报错。
            if not self.llm_api_key.get_secret_value():
                raise ValueError("LLM_PROVIDER=deepseek 时必须配置 LLM_API_KEY")
            if not self.llm_model:
                raise ValueError(
                    "LLM_PROVIDER=deepseek 时必须配置 LLM_MODEL"
                    "（可先请求 GET {LLM_BASE_URL}/models 确认可用模型名）"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
