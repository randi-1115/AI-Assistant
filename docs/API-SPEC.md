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
  "tool_calling": false,
  "web_search": false,
  "document_qa": true,
  "memory": true
}
```

以上仅是返回结构示例，不代表任何未配置服务当前可用。

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
  "search_mode": "auto"
}
```

`mode` 允许值建议：`chat`、`write`、`polish`、`summarize`、`translate`、`brainstorm`、`study`。`search_mode` 为 `auto` 或 `off`。用户启用长期记忆后，服务端才可以读取/写入记忆。该请求保存用户消息，并通过 SSE 返回助手回复。

SSE 事件约定：

```text
event: message_start
data: {"user_message_id":"...","assistant_message_id":"..."}

event: tool_status
data: {"tool":"web_search","status":"running"}

event: token
data: {"text":"这是一段"}

event: source
data: {"title":"来源标题","url":"https://example.com","snippet":"摘要"}

event: message_end
data: {"finish_reason":"stop","usage":{"input_tokens":0,"output_tokens":0}}
```

上述 token 数字是协议示例；若供应商不返回用量，应省略或返回 `null`，不可伪造。错误以 `event: error` 发出，并尽可能保存已有部分内容及中断状态。浏览器停止生成时应取消上游流请求。

### `POST /api/v1/conversations/{conversation_id}/regenerate`
对该会话最后一条助手回答重新生成。若用户编辑旧问题后重发属于后续增强，可再增加编辑消息接口。

## 文件

### `POST /api/v1/files/upload`
`multipart/form-data` 字段：`file`，可选 `conversation_id`。返回 `file_id`、`original_name`、`size_bytes`、`parse_status`、`supported_for_qa`。

首版支持 PDF、DOCX、TXT、MD、常见代码文本扩展名与图片。验证真实文件类型、大小和扩展名；存储名由服务器生成。

### `GET /api/v1/files/{file_id}`
读取文件元数据与解析状态，不返回服务器物理路径。

### `DELETE /api/v1/files/{file_id}`
删除文件元数据、关联分块与服务器文件，先检查归属。

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
