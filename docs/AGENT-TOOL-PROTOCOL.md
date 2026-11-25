# Agent 工具协议与执行契约

## 分层

- **P1 能力层**：文件 RAG（`rag/`）与联网搜索（`search/`）保留原有业务逻辑，并封装成 `document_search`、`web_search` 工具。
- **P2 编排层**：`app/services/agent_service.py` 负责模型自主决定调用哪些工具。
- **统一入口**：`app/services/chat_service.py` 的 `ChatService.run_turn` 负责 `message_start/message_end`、异常归一化、引用校验与落库；两种模式只是可替换的执行器。

`agent_mode` 默认 `off`：
- `off`：P1 预检索路径；
- `auto`：跳过预检索，模型按工具 schema 决定调用。

## 工具契约（`app/tools/base.py`）

```python
class Tool(ABC, Generic[ArgsT]):
    name: ClassVar[str]
    description: ClassVar[str]
    args_model: ClassVar[type[ArgsT]]      # Pydantic，extra="forbid"
    timeout_seconds: float | None = None   # None = 全局上限
    max_output_chars: int | None = None
    def is_available(context) -> bool
    def unavailable_reason(context) -> str | None
    async def run(args, context) -> ToolResult
```

- `ToolContext`：user_id、conversation_id、本轮 attachment_ids、request_id、session_factory。
- `ToolResult`：status（success/empty/unavailable）、content（回填模型）、message、sources、data。
- 工具可预期失败抛 `ToolError`；注册表统一转换，不把异常送进聊天流。

## 注册表与状态映射（`app/tools/registry.py`）

- 白名单 + 运行时 enable/disable；参数校验；单工具超时；输出截断。
- 统一结果 `ToolExecution`，永不上抛（`CancelledError` 除外）。

| ToolExecution.status | SSE `tool_status` | `tool_runs.status` |
|---|---|---|
| success | done | success |
| empty | empty | success |
| unavailable | unavailable | failed（`error_code=tool_unavailable`） |
| failed | failed | failed |
| timeout | failed（`error_code=timeout`） | timeout |

- `tool_runs` 只写终态；`input_json`/`output_summary` 是摘要。
- 工具消息不写入 `messages`；后续轮次不重放工具上下文（P3 边界）。

## 有界循环（`app/services/agent_service.py`）

- `AGENT_MAX_TOOL_CALLS`：单轮**总执行次数**（不是步数）。
- `AGENT_TOTAL_TIMEOUT_SECONDS`：整轮预算；每步 provider 调用用剩余预算收紧。
- 模型一次请求多个工具时逐个执行；每个 `tool_call.id` 都必须有 tool 消息回应。
- 工具失败/参数错误/未知工具/超预算都回填错误，模型决定下一步。
- 达到上限或总超时：追加一次无工具收口调用，并在回答末尾说明原因。
- `asyncio.CancelledError` 永远向上传播。
- 模型不支持原生 tool calling 时走结构化 JSON 协议兜底；工具结果用带边界的 `user` 消息回填。

## 执行器事件契约

`_run_p1` 与 `_run_agent` 共同遵守：
- 只在 `message_start` 之后调用；
- 只 yield `token` / `tool_status` / `source`；
- 可修改 `TurnState`（chunks/usage/finish_reason/status/sources/tool_statuses/steps/tool_calls/limit_reached）；
- 不负责 `message_start/message_end`、引用校验、落库；
- 工具名/参数摘要/耗时/错误码通过 `tool_status` 暴露；终态写入 `metadata_json.tool_statuses`。

## 安全边界

- 工具白名单，不执行任意 Shell/代码/本地路径。
- `calculator` 用 AST 白名单求值，不使用 `eval/exec`。
- `document_search` 只检索本轮 `attachment_ids`，`file_ids` 只能收窄。
- `web_search` 只在用户打开联网搜索（`search_mode=auto`）时暴露。
- 网页/文件内容视为不可信数据，只能作为参考，不能覆盖系统指令。

## Mock 标记

`LLM_PROVIDER=mock` 时，在用户消息里写：

```text
__mock_tool__calculator {"expression":"(1+2)*3"}
```

- 每行一个标记，按顺序确定性推进多步；
- 只在本次请求暴露了 tools 时生效；
- 非法 JSON/未知工具会走真实的失败回填路径。

## 评测

```bash
cd backend
.venv/Scripts/python.exe scripts/eval_p2.py --mode mock
.venv/Scripts/python.exe scripts/eval_p2.py --mode real
```

用例与结果见 `docs/P2-EVALUATION.md`；脚本为 `scripts/eval_p2.py`，用例为 `tests/fixtures/eval/p2_cases.jsonl`。
