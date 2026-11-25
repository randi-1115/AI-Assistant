# API 设计草案

统一前缀：`/api/v1`。以下为建议契约，正式编码前同步维护 Pydantic 与 TypeScript 类型。

## 通用规则

- 普通请求与响应使用 JSON；上传使用 `multipart/form-data`。
- 普通错误格式：`{"error":{"code":"...","message":"...","request_id":"..."}}`。
- 资源 ID 首版可用整数，也可在 API 层统一转换为字符串；不要在同一 API 中混用类型。
- 所有资源查询都必须检查当前用户归属。第一阶段本地单用户以固定开发用户运行；不等同于已完成多用户安全认证。
- 分页参数：`page` 从 1 开始，`page_size` 默认 20，服务端设置最大值。

## 系统

### `GET /health`
返回应用存活状态与版本；不要暴露密钥或数据库凭据。数据库探活可单独返回依赖状态。

### `GET /api/v1/capabilities`
返回当前启用功能及模型能力，例如：

```json
{
  "text_chat": true,
  "streaming": true,
  "vision": false,
  "tool_calling": true,
  "agent_mode": true,
  "web_search": true,
  "document_qa": true,
  "memory": false
}
```

以上仅是返回结构示例，不代表任何未配置服务当前可用。`web_search` 按后端是否配置了 Tavily/SerpApi 的 API Key 返回；未配置时为 `false`，前端应据此禁用联网搜索开关。

`tool_calling` 由当前模型 provider 推导（mock/deepseek 现在为 `true`）；`agent_mode` 表示后端是否启用了工具且有文本聊天能力，前端据此决定"工具模式"开关是否可用。模型不支持原生 tool calling 时仍可用 `agent_mode`，服务端会走结构化输出兜底。

### `GET /api/v1/tools`

只读工具清单，用于调试与后续前端展示。**不执行任何工具**，也不返回用户数据。

```json
{
  "tools": [
    {
      "name": "calculator",
      "description": "计算数学表达式……",
      "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"], "additionalProperties": false},
      "timeout_seconds": 2.0,
      "available": true,
      "unavailable_reason": null
    }
  ]
}
```

- 只列出 `TOOLS_ENABLED` 白名单内的工具；留空时返回空数组。
- `parameters` 是 Pydantic 生成的 JSON Schema，与注册表实际校验规则同源。
- `available` 是当前配置下的可用性（例如未配置搜索 key 时 `web_search` 为 `false`），不可用时 `unavailable_reason` 说明原因。
- 没有工具执行端点：工具调用由服务端 agent 循环决定，不通过 HTTP 暴露。

## 会话和消息

### `POST /api/v1/conversations`
创建会话。请求 `{"title":"新对话"}`，title 可省略，默认“新对话”。响应返回 id、title、created_at、updated_at。

### `GET /api/v1/conversations?keyword=&page=1&page_size=20`
按更新时间倒序返回当前用户的会话；keyword 搜索标题，必要时后续扩展为消息内容搜索。

### `GET /api/v1/conversations/{conversation_id}`
获取会话元数据。

### `PATCH /api/v1/conversations/{conversation_id}`
修改标题：`{"title":"新的标题"}`。

### `DELETE /api/v1/conversations/{conversation_id}`
删除会话及其消息。附件是否随会话删除要按产品规则实现；首版建议删除关联记录并清理无引用文件/文档块。

### `GET /api/v1/conversations/{conversation_id}/messages?before_id=&limit=50`
按时间顺序返回消息。长对话可按游标分页。

### `POST /api/v1/conversations/{conversation_id}/messages/stream`
请求示例：

```json
{
  "content": "总结这份资料，并指出关键结论",
  "attachment_ids": [123],
  "mode": "summarize",
  "search_mode": "auto",
  "agent_mode": "auto"
}
```

`mode` 允许值建议：`chat`、`write`、`polish`、`summarize`、`translate`、`brainstorm`、`study`。`search_mode` 为 `auto` 或 `off`，**默认 `off`**：只有用户显式打开联网搜索时才把问题发送给第三方搜索服务。`auto` 需要后端配置 `SEARCH_PROVIDER` 与对应 API Key；未配置、超时、鉴权失败或空结果都不会中断聊天，只会通过 `tool_status` 和提示词明确告知，不伪造来源。用户启用长期记忆后，服务端才可以读取/写入记忆。该请求保存用户消息，并通过 SSE 返回助手回复。

`agent_mode` 为 `auto` 或 `off`，**默认 `off`**：
- `off`：保持 P1 预检索行为（附件片段与联网结果在调用模型前注入）。
- `auto`：跳过预检索，由模型按工具 schema 决定调用 `calculator` / `document_search` / `web_search`；`search_mode=auto` 时才向模型暴露 `web_search`，没有附件时不暴露 `document_search`。
- 工具调用有 `AGENT_MAX_TOOL_CALLS` 次数上限与 `AGENT_TOTAL_TIMEOUT_SECONDS` 总超时；工具失败、参数错误、未知工具都会作为 tool 结果回填模型后继续，不中断聊天。
- 模型不支持原生 tool calling 时，服务端走结构化 JSON 协议兜底，对外事件格式不变。
- 重新生成没有请求体，`agent_mode` / `search_mode` / 附件从最后一条用户消息的 `metadata_json` 继承。

`attachment_ids` 只接受当前用户自己的文件，去重后最多 10 个；**只有这些文件参与本轮检索**（`agent_mode=auto` 时只有这些文件可被 `document_search` 检索），不携带附件时不启用 RAG。附件属于其他会话、不存在或不属于当前用户时返回 404；文件已解析失败时不会阻断聊天，只会跳过检索并在提示词中说明资料不足。

SSE 事件约定：

```text
event: message_start
data: {"user_message_id":"...","assistant_message_id":"..."}

event: tool_status
data: {"tool":"web_search","status":"running","call_id":"call_1","arguments_summary":"query=\"最近的 AI 新闻\""}

event: source
data: {"kind":"file","file_id":123,"chunk_index":4,"title":"报告.pdf","url":null,"section_title":"第 2 页","snippet":"……","score":0.87,"excerpt_number":1,"citation_label":"[资料 1]"}

event: source
data: {"kind":"web","title":"示例标题","url":"https://example.com/a","snippet":"网页摘要","published_date":"2026-11-12","domain":"example.com","score":0.83}

event: tool_status
data: {"tool":"web_search","status":"done","message":"已通过 tavily 找到 3 条结果","provider":"tavily","call_id":"call_1","arguments_summary":"query=\"最近的 AI 新闻\"","duration_ms":2310,"error_code":null}

event: token
data: {"text":"这是一段"}

event: message_end
data: {"finish_reason":"stop","usage":{"input_tokens":0,"output_tokens":0}}
```

`source` 默认在 `message_start` 之后、首个 `token` 之前逐条发出，顺序即检索排名：文件来源在前，联网来源在后。`agent_mode=auto` 时来源会在工具执行完成后立即发出，因此可能出现在 `token` 之间；重复来源只发一次，文件片段按 `(file_id, chunk_index)` 去重并全局连续编号。文件来源没有下载端点，`url` 固定为 `null`；联网来源 `url` 必填，并携带 `domain`、规范化后的 `published_date` 与本地排序分数 `score`。文件来源的 `excerpt_number` / `citation_label` 对应提示词里的 `[资料 N]`，只返回实际进入提示词的片段。助手消息的 `metadata_json.sources` 保存同一批来源，`metadata_json.tool_statuses` 保存工具终态（`done`/`empty`/`unavailable`/`failed`），`metadata_json.citation_report` 记录回答中 `[资料 N]` 的引用校验结果；`agent_mode=auto` 时 `metadata_json.agent` 记录 `steps` / `tool_calls` / `limit_reached`；刷新历史后可恢复展示。`running` 只是流式瞬态事件，不落库。搜索失败或空结果时不会产生 web `source`。

`tool_status` 在 agent 模式下会按工具调用逐个出现，字段：
- `call_id`：本轮调用 id，`running` 与终态配对；
- `arguments_summary`：参数摘要（已截断，不是完整 JSON）；
- `duration_ms`：执行耗时；
- `error_code`：失败原因，例如 `invalid_arguments`、`unknown_tool`、`tool_budget_exceeded`、`timeout`、工具自定义 code。
超时统一用 `status="failed"` + `error_code="timeout"` 表示。`tool_runs` 表记录每次工具执行的终态（`success`/`failed`/`timeout`），不写 `running`。

`tool_budget_exceeded` 只在**模型一次响应请求的调用数超过剩余预算**时出现；恰好用完 `AGENT_MAX_TOOL_CALLS` 不会出现该错误码，而是结束循环、追加一次无工具收口调用，并在回答末尾说明原因；助手 metadata 的 `agent.limit_reached` 会记录该情况。

上述 token 数字是协议示例；若供应商不返回用量，应省略或返回 `null`，不可伪造。错误以 `event: error` 发出，并尽可能保存已有部分内容及中断状态。浏览器停止生成时应取消上游流请求。

### `POST /api/v1/conversations/{conversation_id}/regenerate`
对该会话最后一条助手回答重新生成。若用户编辑旧问题后重发属于后续增强，可再增加编辑消息接口。

## 文件

### `POST /api/v1/files/upload`
`multipart/form-data` 字段：`file` 必填，可选 `conversation_id`。成功返回 `201`：

```json
{
  "file_id": 12,
  "original_name": "报告.pdf",
  "size_bytes": 123456,
  "parse_status": "ready",
  "supported_for_qa": true,
  "error_message": null
}
```

首版支持 PDF、DOCX、TXT、MD/RST、CSV/JSON/YAML/TOML/INI/XML/HTML/CSS 与常见代码文本扩展名；**图片在阶段 2 不接受**，返回 415。验证真实文件类型（魔数/容器结构）、大小和扩展名；存储名由服务器生成，用户文件名不参与磁盘路径。单文件上限默认 20MB。

解析在上传请求内同步完成：返回时 `parse_status` 已是 `ready` 或 `failed`。内容无法解析（空文本、扫描版 PDF、损坏文档）返回 `201` 且 `parse_status="failed"`、`error_message` 说明原因，文件保留供用户查看状态；扩展名/真实类型不匹配或超限则直接拒绝，不落库。

错误码：`413 file_too_large`、`415 unsupported_file_type`、`415 invalid_file_content`、`422 invalid_file_name`、`404 conversation_not_found`。

### `GET /api/v1/files/{file_id}`
读取文件元数据与解析状态，返回 `file_id`、`conversation_id`、`original_name`、`mime_type`、`extension`、`size_bytes`、`parse_status`、`supported_for_qa`、`error_message`、`created_at`；不返回 `storage_key` 或服务器物理路径。不存在或不属于当前用户均返回 404。

### `DELETE /api/v1/files/{file_id}`
先检查归属，删除文件元数据、关联分块与服务器文件，返回 `204`。删除会话时，其附件、分块与磁盘文件会级联删除。

## 记忆与设置

### `GET /api/v1/settings/memory`
返回 `{ "enabled": false }` 等状态。

### `PUT /api/v1/settings/memory`
请求 `{ "enabled": true }`。默认 `false`。关闭之后停止新增及注入记忆；是否清除已保存内容由独立确认操作决定。

### `GET /api/v1/memories?page=1&page_size=20`
列出当前用户记忆。

### `PATCH /api/v1/memories/{memory_id}`
修改记忆内容/分类/启用状态。

### `DELETE /api/v1/memories/{memory_id}`
删除一条记忆。

## 未来接口（不进入首版）

- 账号注册、登录、登出、刷新 token。
- 用户自定义模型与多供应商配置 UI。
- 语音、图片生成、代码沙箱、云端同步、知识库管理界面。

这些接口只有在对应产品需求确认后再定，不先制造没有使用场景的 API。
