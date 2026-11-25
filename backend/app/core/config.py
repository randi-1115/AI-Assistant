"""集中式应用配置。

所有配置项都从后端环境变量或 backend/.env 读取，密钥只出现在后端。
前端环境变量（VITE_ 前缀）会打包进浏览器，绝不放服务商密钥。
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> parents[2] == backend/
BACKEND_DIR = Path(__file__).resolve().parents[2]

SUPPORTED_LLM_PROVIDERS = ("mock", "deepseek")
# 联网搜索只接两家：Tavily 为主，SerpApi 可选作为备源。
SUPPORTED_SEARCH_PROVIDERS = ("none", "tavily", "serpapi")
# 工具名只允许小写字母/数字/下划线，避免空格、路径和注入字符进入白名单。
_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


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
    mock_stream_delay_ms: int = 60 # 模拟流式响应延迟，单位毫秒
    mock_chunk_size: int = 4 # 模拟流式响应分块大小，单位字符
    mock_default_reply: str = (
        "这是 mock provider 的固定回答。当前 LLM_PROVIDER=mock，"
        "没有调用任何真实模型；把 .env 里的 LLM_PROVIDER 改成 deepseek 并填好 "
        "LLM_API_KEY 之后，才会走真实模型。"
    )
    # 全局强制触发错误，用于手工验收错误分支：upstream|timeout|rate_limit|auth
    mock_force_error: str = ""

    # --- 联网搜索 ---
    # none 表示关闭；tavily 为主源；serpapi 可选作为 fallback。
    search_provider: str = "none"
    search_fallback_provider: str = ""
    tavily_api_key: SecretStr = SecretStr("")
    serpapi_api_key: SecretStr = SecretStr("")
    search_timeout_seconds: float = 8.0
    search_max_results: int = 5
    search_snippet_chars: int = 300
    search_context_max_chars: int = 3000

    # --- 查询规划与质量硬化（P1.5）---
    search_query_plan_enabled: bool = True
    search_llm_planning_enabled: bool = True # 测试环境设 false
    search_planner_timeout_seconds: float = 5.0
    search_planner_max_tokens: int = 256
    search_subqueries_enabled: bool = True
    search_max_subqueries: int = 2
    search_fetch_results: int = 10 # 过取数量，最终仍取 search_max_results
    search_per_domain_max_results: int = 2
    search_total_timeout_seconds: float = 12.0
    search_cache_ttl_seconds: int = 300
    search_cache_max_entries: int = 128
    search_extra_authority_domains: str = ""
    search_include_raw_content: bool = True
    search_content_chars: int = 1200

    # --- 本地文件 ---
    upload_dir: str = "../data/uploads"
    max_upload_mb: int = 20 # 最大上传文件大小，单位 MB
    rag_top_k: int = 4 # RAG 模型返回的 top-k 个文档

    # --- 文档解析与切块（阶段 P1：文件上传 + 轻量 RAG）---
    rag_chunk_size_chars: int = 800 # 单个文本块的目标长度（字符）
    rag_chunk_overlap_chars: int = 100 # 相邻块重叠长度（字符）
    rag_max_chunks_per_file: int = 500 # 单文件最多切多少块，防止超大文件打爆库
    rag_max_extracted_chars: int = 1_000_000 # 单文件最多保留多少字符
    rag_max_candidate_chunks: int = 2000 # 单次检索最多从库里捞多少候选块
    rag_candidates_per_file: int = 400 # 每个附件最多取多少候选，避免旧文件被挤掉
    rag_context_max_chars: int = 4000 # 拼进提示词的资料片段总预算
    rag_source_snippet_chars: int = 300 # SSE / 元数据里的来源摘要长度
    rag_max_excerpts_per_file: int = 2 # 多文件时每个文件最多进几个 excerpt
    rag_citation_validation: bool = True # 记录回答里的 [资料 N] 是否有效
    rag_synonym_expansion: bool = False # 同义词扩展默认关闭

    # --- P2 工具协议（白名单、单工具超时、输出上限）---
    # 逗号分隔的工具白名单；留空表示禁用全部工具。
    # 名称格式在这里只做语法校验，是否真的注册由 tools 层决定。
    tools_enabled: str = "calculator,document_search,web_search"
    # 单轮 agent 循环最多执行多少次工具调用。
    agent_max_tool_calls: int = 3
    # 单轮 agent 循环的总时间预算，必须给多步调用留出空间。
    agent_total_timeout_seconds: float = 90.0
    # 默认单工具超时必须大于 SEARCH_TOTAL_TIMEOUT_SECONDS，否则联网搜索会被提前掐断。
    tool_timeout_seconds: float = 15.0
    # 工具结果回填模型前的硬上限；单个工具可以设得更小。
    tool_max_output_chars: int = 8000

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def tools_enabled_names(self) -> frozenset[str]:
        return frozenset(
            item.strip().lower() for item in self.tools_enabled.split(",") if item.strip()
        )

    @model_validator(mode="after") # 检查 LLM 配置是否合法
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

    @model_validator(mode="after")  # 检查 RAG 参数是否自洽
    def _check_rag_config(self) -> "Settings":
        if self.rag_chunk_size_chars <= 0:
            raise ValueError("RAG_CHUNK_SIZE_CHARS 必须大于 0")
        if not 0 <= self.rag_chunk_overlap_chars < self.rag_chunk_size_chars:
            raise ValueError("RAG_CHUNK_OVERLAP_CHARS 必须在 [0, RAG_CHUNK_SIZE_CHARS) 内")
        if self.rag_top_k < 1:
            raise ValueError("RAG_TOP_K 必须大于等于 1")
        if self.rag_max_chunks_per_file < 1:
            raise ValueError("RAG_MAX_CHUNKS_PER_FILE 必须大于等于 1")
        if self.rag_max_extracted_chars < 1:
            raise ValueError("RAG_MAX_EXTRACTED_CHARS 必须大于等于 1")
        if self.rag_max_candidate_chunks < 1:
            raise ValueError("RAG_MAX_CANDIDATE_CHUNKS 必须大于等于 1")
        if not 1 <= self.rag_candidates_per_file <= self.rag_max_candidate_chunks:
            raise ValueError("RAG_CANDIDATES_PER_FILE 必须在 [1, RAG_MAX_CANDIDATE_CHUNKS] 内")
        if self.rag_max_excerpts_per_file < 1:
            raise ValueError("RAG_MAX_EXCERPTS_PER_FILE 必须大于等于 1")
        if not 0 < self.rag_context_max_chars <= 20000:
            raise ValueError("RAG_CONTEXT_MAX_CHARS 必须在 (0, 20000] 内")
        if not 0 < self.rag_source_snippet_chars <= 2000:
            raise ValueError("RAG_SOURCE_SNIPPET_CHARS 必须在 (0, 2000] 内")
        return self

    @model_validator(mode="after")  # 检查联网搜索参数是否自洽
    def _check_search_config(self) -> "Settings":
        if self.search_provider not in SUPPORTED_SEARCH_PROVIDERS:
            raise ValueError(
                f"SEARCH_PROVIDER 取值非法：{self.search_provider!r}，"
                f"只支持 {SUPPORTED_SEARCH_PROVIDERS}"
            )
        if self.search_fallback_provider and (
            self.search_fallback_provider not in SUPPORTED_SEARCH_PROVIDERS
            or self.search_fallback_provider == "none"
        ):
            raise ValueError("SEARCH_FALLBACK_PROVIDER 只能是 tavily 或 serpapi，或留空")
        if not 0 < self.search_timeout_seconds <= 30:
            raise ValueError("SEARCH_TIMEOUT_SECONDS 必须在 (0, 30] 内")
        if not 1 <= self.search_max_results <= 10:
            raise ValueError("SEARCH_MAX_RESULTS 必须在 [1, 10] 内")
        if not 0 < self.search_snippet_chars <= 1000:
            raise ValueError("SEARCH_SNIPPET_CHARS 必须在 (0, 1000] 内")
        if not 0 < self.search_context_max_chars <= 8000:
            raise ValueError("SEARCH_CONTEXT_MAX_CHARS 必须在 (0, 8000] 内")
        if not self.search_max_results <= self.search_fetch_results <= 20:
            raise ValueError("SEARCH_FETCH_RESULTS 必须在 [SEARCH_MAX_RESULTS, 20] 内")
        if not 0 < self.search_total_timeout_seconds <= 30:
            raise ValueError("SEARCH_TOTAL_TIMEOUT_SECONDS 必须在 (0, 30] 内")
        if self.search_per_domain_max_results < 1:
            raise ValueError("SEARCH_PER_DOMAIN_MAX_RESULTS 必须大于等于 1")
        if not 0 <= self.search_content_chars <= 5000:
            raise ValueError("SEARCH_CONTENT_CHARS 必须在 [0, 5000] 内")
        if not 0 <= self.search_max_subqueries <= 5:
            raise ValueError("SEARCH_MAX_SUBQUERIES 必须在 [0, 5] 内")
        if not 0 <= self.search_cache_ttl_seconds <= 3600:
            raise ValueError("SEARCH_CACHE_TTL_SECONDS 必须在 [0, 3600] 内")
        if not 0 < self.search_planner_timeout_seconds <= 10:
            raise ValueError("SEARCH_PLANNER_TIMEOUT_SECONDS 必须在 (0, 10] 内")
        if not 64 <= self.search_planner_max_tokens <= 1024:
            raise ValueError("SEARCH_PLANNER_MAX_TOKENS 必须在 [64, 1024] 内")
        return self

    @model_validator(mode="after")  # 检查 P2 工具白名单与上限是否合法
    def _check_tool_config(self) -> "Settings":
        if not 0 < self.tool_timeout_seconds <= 60:
            raise ValueError("TOOL_TIMEOUT_SECONDS 必须在 (0, 60] 内")
        if not 256 <= self.tool_max_output_chars <= 50_000:
            raise ValueError("TOOL_MAX_OUTPUT_CHARS 必须在 [256, 50000] 内")
        for name in self.tools_enabled_names:
            if not _TOOL_NAME.match(name):
                raise ValueError(f"TOOLS_ENABLED 中的工具名非法：{name!r}")
        if (
            "web_search" in self.tools_enabled_names
            and self.tool_timeout_seconds <= self.search_total_timeout_seconds
        ):
            raise ValueError(
                "TOOL_TIMEOUT_SECONDS 必须大于 SEARCH_TOTAL_TIMEOUT_SECONDS，"
                "否则联网搜索会被工具超时提前掐断"
            )
        return self

    @model_validator(mode="after")  # 检查 agent 循环边界
    def _check_agent_config(self) -> "Settings":
        if not 1 <= self.agent_max_tool_calls <= 10:
            raise ValueError("AGENT_MAX_TOOL_CALLS 必须在 [1, 10] 内")
        if not 0 < self.agent_total_timeout_seconds <= 300:
            raise ValueError("AGENT_TOTAL_TIMEOUT_SECONDS 必须在 (0, 300] 内")
        if self.agent_total_timeout_seconds < self.tool_timeout_seconds:
            raise ValueError(
                "AGENT_TOTAL_TIMEOUT_SECONDS 不能小于 TOOL_TIMEOUT_SECONDS，"
                "否则工具还没跑完总预算就耗尽"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
