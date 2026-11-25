# MySQL 数据库表结构草案

建议 MySQL 8.x、`utf8mb4`、InnoDB。具体数据库版本需在本地环境确认。表结构通过 Alembic 迁移维护，避免直接在生产库手改。

## 关系概览

```text
users 1 ── N conversations 1 ── N messages
users 1 ── N attachments 1 ── N document_chunks
users 1 ── N user_memories
messages 1 ── N tool_runs
users 1 ── 1 user_settings
```

首版即预留 `user_id`，所有业务查询按用户过滤。当前开发模式使用一个固定 local user；它不是完整认证，公开部署前必须增加认证和授权。

## 表与关键字段

### `users` — 预留用户主体

- `id BIGINT UNSIGNED PK AUTO_INCREMENT`
- `display_name VARCHAR(100) NOT NULL`
- `email VARCHAR(255) NULL UNIQUE`
- `password_hash VARCHAR(255) NULL`（首版不用来登录）
- `status VARCHAR(20) NOT NULL DEFAULT 'active'`
- `created_at DATETIME(3) NOT NULL`
- `updated_at DATETIME(3) NOT NULL`

本地初始化创建一条开发用户。未实现密码登录前不能把空密码字段解释为安全账号。

### `user_settings` — 用户设置

- `user_id BIGINT UNSIGNED PK, FK -> users.id`
- `memory_enabled BOOLEAN NOT NULL DEFAULT FALSE`
- `settings_json JSON NULL`
- `created_at DATETIME(3) NOT NULL`
- `updated_at DATETIME(3) NOT NULL`

### `conversations` — 会话

- `id BIGINT UNSIGNED PK AUTO_INCREMENT`
- `user_id BIGINT UNSIGNED NOT NULL FK -> users.id`
- `title VARCHAR(200) NOT NULL DEFAULT '新对话'`
- `status VARCHAR(20) NOT NULL DEFAULT 'active'`
- `created_at DATETIME(3) NOT NULL`
- `updated_at DATETIME(3) NOT NULL`
- 索引：`(user_id, updated_at)`

### `messages` — 消息

- `id BIGINT UNSIGNED PK AUTO_INCREMENT`
- `conversation_id BIGINT UNSIGNED NOT NULL FK -> conversations.id`
- `role VARCHAR(20) NOT NULL`：`user`、`assistant`、必要时 `tool`
- `content LONGTEXT NOT NULL`
- `status VARCHAR(20) NOT NULL DEFAULT 'complete'`：如 `streaming`、`complete`、`interrupted`、`failed`
- `model_provider VARCHAR(100) NULL`
- `model_name VARCHAR(150) NULL`
- `metadata_json JSON NULL`：来源、附件、finish reason 等非核心结构化元数据
- `created_at DATETIME(3) NOT NULL`
- 索引：`(conversation_id, id)`

### `attachments` — 上传文件元数据

- `id BIGINT UNSIGNED PK AUTO_INCREMENT`
- `user_id BIGINT UNSIGNED NOT NULL FK -> users.id`
- `conversation_id BIGINT UNSIGNED NULL FK -> conversations.id`
- `original_name VARCHAR(255) NOT NULL`
- `storage_key VARCHAR(500) NOT NULL`：内部相对键，不向客户端暴露真实路径
- `mime_type VARCHAR(150) NULL`
- `extension VARCHAR(20) NOT NULL`
- `size_bytes BIGINT UNSIGNED NOT NULL`
- `sha256 CHAR(64) NULL`
- `parse_status VARCHAR(20) NOT NULL DEFAULT 'pending'`：`pending`、`processing`、`ready`、`failed`、`unsupported`
- `error_message TEXT NULL`
- `created_at DATETIME(3) NOT NULL`
- 索引：`(user_id, created_at)`、`(conversation_id)`
- 外键行为：`user_id` 随用户删除级联；`conversation_id` 随会话删除级联（磁盘文件由应用层同步清理）。上传时还没有会话的附件在首次作为聊天附件使用时归属到当前会话。
- 阶段 2 只在当前用户显式附带 `attachment_ids` 的文件范围内检索；不做跨会话复用。

### `document_chunks` — 文档分块

- `id BIGINT UNSIGNED PK AUTO_INCREMENT`
- `attachment_id BIGINT UNSIGNED NOT NULL FK -> attachments.id`
- `chunk_index INT UNSIGNED NOT NULL`
- `section_title VARCHAR(255) NULL`
- `content MEDIUMTEXT NOT NULL`
- `char_count INT UNSIGNED NOT NULL`
- `metadata_json JSON NULL`
- `created_at DATETIME(3) NOT NULL`
- 唯一索引：`(attachment_id, chunk_index)`
- 外键行为：`attachment_id` 随附件删除级联。

首版对检索候选执行关键词重合度评分（中文二元组 + 英文/代码词，覆盖率/词频/短语/文件名加权）；表结构不意味着已经接入 embedding 或向量数据库。当前实现没有全文索引，候选块按用户与附件范围取出后在应用层打分。

### `user_memories` — 长期记忆

- `id BIGINT UNSIGNED PK AUTO_INCREMENT`
- `user_id BIGINT UNSIGNED NOT NULL FK -> users.id`
- `content TEXT NOT NULL`
- `category VARCHAR(50) NULL`：例如 preference、goal、project
- `importance DECIMAL(3,2) NOT NULL DEFAULT 0.50`
- `source_conversation_id BIGINT UNSIGNED NULL FK -> conversations.id`
- `is_active BOOLEAN NOT NULL DEFAULT TRUE`
- `created_at DATETIME(3) NOT NULL`
- `updated_at DATETIME(3) NOT NULL`
- `last_used_at DATETIME(3) NULL`
- 索引：`(user_id, is_active, updated_at)`

### `tool_runs` — Agent 工具运行记录

- `id BIGINT UNSIGNED PK AUTO_INCREMENT`
- `message_id BIGINT UNSIGNED NOT NULL FK -> messages.id`
- `tool_name VARCHAR(100) NOT NULL`
- `input_json JSON NULL`
- `output_summary TEXT NULL`
- `status VARCHAR(20) NOT NULL`：`success`、`failed`、`timeout`
- `error_code VARCHAR(100) NULL`：稳定的机器码，例如 `invalid_arguments`、`unknown_tool`、`tool_budget_exceeded`
- `error_message TEXT NULL`
- `duration_ms INT UNSIGNED NULL`
- `created_at DATETIME(3) NOT NULL`
- 索引：`(message_id, created_at)`

> 已实现（迁移 `c1d2e3f4a5b6`）。只写**终态**：`running` 是流式瞬态，没有可更新的独立行，写进去只会留下永远不结束的记录。`input_json` / `output_summary` 是摘要，不保存完整工具输出。删除消息/会话时 `tool_runs` 级联删除。

## 建表原则

1. 外键约束与删除行为要在 SQLAlchemy/Alembic 中明确。删除会话时，先删除消息与工具记录；文件若可跨会话使用则不能盲目删除，应定义好归属规则。
2. 所有业务 SQL 都通过 repository 或受控查询执行；不能仅靠前端传来的 ID 判断归属。
3. 文件正文保存在私有文件存储，文档片段存数据库；不把文件路径直接返回给用户。
4. 长对话消息采用分页；构造模型上下文时只取最近适当窗口并控制 token 数量。
5. 数据库密码使用环境变量，迁移脚本不写死生产凭据。
6. 为控制成本，后续可增加 `model_usage_logs` 表记录 token 数量、模型名与估算费用；具体计费字段取决于所选供应商返回的数据。
