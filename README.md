# AI Assistant V1

豆包式网页版个人 AI 助手（视频除外）：左侧会话列表、中间消息区、底部输入区。

同时作为 **AI Agent 应用开发校招/实习作品**：重点不是功能数量，而是上下文、检索、工具调用、评测与安全的工程闭环。

## 当前进度

| 阶段 | 主题 | 状态 |
|---|---|---|
| P0 | 会话、多轮聊天、SSE、停止/重生成、Markdown、MySQL、错误/日志 | **已完成** |
| P1 | 文件上传/解析/切块/关键词 RAG、Tavily+SerpApi 联网搜索、来源与降级 | **已完成** |
| P1.5 | 真实文档/搜索评测、正文抽取、查询规划、来源多样性、延迟/成本门槛 | **已完成（有已知边界）** |
| P2 | Agent 核心：工具协议、function calling、有界循环、tool_run 日志、工具状态 UI | **下一步** |
| P3 | 长期记忆、设置、创作模式、自定义指令 | 计划 |
| P4 | 图像理解、语音输入/朗读（不做图像生成） | 计划 |
| P5 | 多模型切换、认证/多用户、部署、限流、可观测性、CI | 计划 |
| P6 | MCP/插件、工作模式、可选多 Agent | 计划 |
| P7 | 视频、图像生成/编辑、训练/微调、社交/支付 | 暂缓 |

> P1 的文件问答与联网搜索已在当前工作区实现；P2 的 `tools/`、`agent_service.py` 仍是占位。

## 功能矩阵

| 能力 | 状态 |
|---|---|
| 文本聊天、多轮、SSE、停止、重新生成、自动标题 | 已实现 |
| 会话 CRUD、历史、Markdown/代码高亮、统一错误/请求 ID | 已实现 |
| 文件上传（PDF/DOCX/TXT/MD/代码）、私有存储、解析、切块 | 已实现 |
| 关键词 RAG、文件来源、总结类基线、失败状态 | 已实现 |
| 联网搜索（Tavily 主源 + SerpApi 可选备源）、来源、时效参数、降级 | 已实现 |
| 搜索正文抽取、查询规划、来源质量/多样性、缓存/预算 | 已实现（P1.5） |
| 文件标题路径、表格、overlap 预算、IDF/引用校验 | 已实现（P1.5） |
| Agent 工具调用、工具日志、有界循环 | 下一步（P2） |
| 长期记忆、创作模式、设置页 | 计划（P3） |
| 图像理解、语音输入/朗读 | 计划（P4） |
| 多模型切换、认证/多用户、部署 | 计划（P5） |
| 视频、图像生成/编辑 | 暂缓 |

## 技术路线

| 层 | 选型 |
|---|---|
| 前端 | Vue 3 + TypeScript + Vite（Pinia、Vue Router、markdown-it、highlight.js） |
| 后端 | Python 3.12 + FastAPI + SQLAlchemy 2.x（async） |
| 数据库 | MySQL 8.x + Alembic 迁移 |
| 模型 | DeepSeek（OpenAI 兼容协议）；另有 mock provider 供离线开发与测试 |
| 搜索 | Tavily 主源 + SerpApi 可选 fallback |
| 文件存储 | 本地私有目录（已接入上传/删除） |
| 流式 | SSE（`text/event-stream`） |

## 环境要求

- Python **3.12**（不要用 3.9/Anaconda，依赖装不上）
- Node.js 20+
- MySQL 8.x，且服务已启动

## 本地启动

### 1. 启动 MySQL

```powershell
# 需要管理员权限
net start MySQL80
```

建两个库（`ai_assistant` 给开发用，`ai_assistant_test` 给集成测试用）：

```bash
mysql -uroot -p -e "CREATE DATABASE IF NOT EXISTS ai_assistant CHARACTER SET utf8mb4;"
mysql -uroot -p -e "CREATE DATABASE IF NOT EXISTS ai_assistant_test CHARACTER SET utf8mb4;"
```

### 2. 后端

```bash
cd backend
py -3.12 -m venv .venv
.venv/Scripts/activate            # Windows；Linux/macOS 用 source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env              # 然后编辑 .env
```

`backend/.env` 里至少要改这两项：

```ini
DATABASE_URL=mysql+aiomysql://root:你的密码@127.0.0.1:3306/ai_assistant?charset=utf8mb4
TEST_DATABASE_URL=mysql+aiomysql://root:你的密码@127.0.0.1:3306/ai_assistant_test?charset=utf8mb4
```

跑迁移并启动：

```bash
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

验证：

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok","version":"0.1.0","dependencies":{"database":"ok"}}
```

接口文档在 <http://127.0.0.1:8000/docs>。

### 3. 前端

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
```

开发时 Vite 会把 `/api` 代理到 `http://127.0.0.1:8000`，前后端同源，不涉及跨域。

## 切换真实模型

默认 `LLM_PROVIDER=mock`，**不联网、不消耗额度**，回答是固定的。要接真实模型：

1. 先确认模型名（**不要凭记忆写**）：

   ```bash
   curl -H "Authorization: Bearer <你的Key>" https://api.deepseek.com/models
   ```

2. 编辑 `backend/.env`：

   ```ini
   LLM_PROVIDER=deepseek
   LLM_API_KEY=<你的Key>
   LLM_MODEL=<上一步确认到的模型名>
   ```

3. 重启后端。缺任何一项，应用会在**启动时**直接报错并指出缺哪个——这是刻意的快速失败，好过第一次聊天才炸。

密钥只放在 `backend/.env`（已被 `.gitignore` 忽略），绝不进前端环境变量——`VITE_` 前缀的值会打包进浏览器。

## 联网搜索配置

联网搜索默认关闭。填好 `TAVILY_API_KEY` 后把 `SEARCH_PROVIDER` 改为 `tavily`；如果还想在主源失败时自动切换，再填 `SERPAPI_API_KEY` 并设置 `SEARCH_FALLBACK_PROVIDER=serpapi`。

```ini
SEARCH_PROVIDER=tavily
TAVILY_API_KEY=tvly-...
SEARCH_FALLBACK_PROVIDER=serpapi
SERPAPI_API_KEY=...
```

未配置 key 时前端「联网搜索」开关会置灰；即使直接发 `search_mode=auto`，也只会降级提示，不会伪造来源。

## 主要环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `DATABASE_URL` | 必填 | MySQL 连接串，驱动用 `aiomysql` |
| `TEST_DATABASE_URL` | 空 | 集成测试库；留空则集成测试整体跳过 |
| `LLM_PROVIDER` | `mock` | `mock` 或 `deepseek` |
| `LLM_API_KEY` / `LLM_MODEL` | 空 | `deepseek` 模式下必填 |
| `UPLOAD_DIR` / `MAX_UPLOAD_MB` | `../data/uploads` / 20 | 本地私有上传目录 / 单文件大小上限 |
| `RAG_TOP_K` / `RAG_CHUNK_SIZE_CHARS` / `RAG_CHUNK_OVERLAP_CHARS` | 4 / 800 / 100 | 检索片段数 / 切块长度 / 重叠 |
| `RAG_CONTEXT_MAX_CHARS` / `RAG_MAX_EXTRACTED_CHARS` | 4000 / 1000000 | 文件资料预算 / 单文件提取字符上限 |
| `SEARCH_PROVIDER` / `SEARCH_FALLBACK_PROVIDER` | `none` / 空 | 联网搜索主源 / 可选备源 |
| `TAVILY_API_KEY` / `SERPAPI_API_KEY` | 空 | 对应搜索服务的密钥 |
| `SEARCH_TIMEOUT_SECONDS` / `SEARCH_MAX_RESULTS` | 8 / 5 | 单次搜索超时 / 最终返回结果数上限（1–10） |
| `SEARCH_FETCH_RESULTS` / `SEARCH_PER_DOMAIN_MAX_RESULTS` | 10 / 2 | 过取候选数 / 每域名最多保留数 |
| `SEARCH_TOTAL_TIMEOUT_SECONDS` | 12 | 规划 + 主查询 + 子查询总预算 |
| `SEARCH_INCLUDE_RAW_CONTENT` / `SEARCH_CONTENT_CHARS` | true / 1200 | Tavily 正文抽取开关 / 每条正文字符上限 |
| `SEARCH_LLM_PLANNING_ENABLED` / `SEARCH_MAX_SUBQUERIES` | true / 2 | 低置信度时 LLM 规划兜底 / 宽泛新闻子查询数 |
| `SEARCH_CONTEXT_MAX_CHARS` / `SEARCH_SNIPPET_CHARS` | 3000 / 300 | 网页资料预算 / 来源摘要长度 |
| `RAG_CANDIDATES_PER_FILE` / `RAG_MAX_EXCERPTS_PER_FILE` | 400 / 2 | 每附件候选块 / 多文件时每文件 excerpt 上限 |
| `CONTEXT_MAX_MESSAGES` / `CONTEXT_MAX_CHARS` | 20 / 8000 | 历史消息条数 / 上下文字符预算 |
| `MOCK_FORCE_ERROR` | 空 | 强制 mock 报错：`upstream`/`timeout`/`rate_limit`/`auth` |

完整列表见 `backend/.env.example`。

## 测试

```bash
# 后端：单元测试不需要数据库和网络
cd backend
pytest tests/unit

# 后端：集成测试需要 MySQL 测试库（见 TEST_DATABASE_URL）
pytest tests/integration

# 前端：SSE/来源/工具状态解析
cd frontend
npm test
npm run type-check
npm run build
```

单元测试与集成测试默认使用 mock LLM 和 fake search provider，**不依赖真实 API，不消耗 token**。
真实模型/搜索/文档质量由 P1.5 评测脚本单独验证；不能用 fake provider 的绿灯替代真实质量结论。

```bash
cd backend

# 离线文件评测（不联网）
python scripts/eval_p15.py --mode offline

# 真实搜索评测（消耗 Tavily/SerpApi 额度）
python scripts/eval_p15.py --mode search --real

# 完整真实 E2E（真实 LLM + 搜索 + 文件；会消耗额度）
python scripts/eval_p15.py --mode e2e --real --max-cases 3
```

评测用例在 `backend/tests/fixtures/eval/`，生成文档在 `tests/fixtures/documents.py`；脚本默认清理 E2E 会话。

## 项目结构

```text
ai-assistant-v1/
├── docs/                  # PRD、API 契约、数据库表结构、开发路线图
├── backend/
│   ├── alembic/           # 数据库迁移
│   └── app/
│       ├── api/           # HTTP 层：参数校验、调服务、返回
│       ├── core/          # 配置、日志、统一错误
│       ├── db/            # 引擎、会话、基类、启动种子
│       ├── models/        # SQLAlchemy ORM
│       ├── repositories/  # SQL 查询（强制按 user_id 过滤）
│       ├── schemas/       # Pydantic 请求/响应契约
│       ├── services/      # 聊天、会话、文件、搜索；agent_service 待实现
│       ├── ai/providers/  # LLM 抽象、mock、OpenAI 兼容 adapter
│       ├── rag/           # 解析、切块、关键词检索、RAG 提示词（已实现）
│       ├── search/        # Tavily/SerpApi、主备切换、搜索提示词（已实现）
│       ├── storage/       # 私有存储抽象与本地实现（已实现）
│       └── tools/         # Agent 工具协议/注册表（P2 下一步）
├── frontend/src/
│   ├── api/               # 请求封装、SSE 解析
│   ├── components/        # 布局、聊天、上传、来源、工具状态
│   ├── stores/            # Pinia：会话列表、消息与流式状态
│   ├── types/             # 与后端 schema 对应的类型
│   ├── views/             # 页面
│   └── utils/             # Markdown、metadata 解析
├── backend/scripts/       # eval_p15.py 质量评测脚本
├── backend/tests/fixtures/ # 评测文档生成器与 query/doc 用例
└── data/uploads/          # 本地上传目录（已使用）
```

## 演示场景

1. **文件总结**：上传真实 DOCX/PDF/TXT → 问“总结这个文件” → 回答下方出现文件来源（文件名、片段、章节）。
2. **文件细节**：针对具体段落/数字提问 → 召回对应片段，资料不足时明确说明。
3. **时效新闻**：打开联网搜索 → 问“最近一周 AI 圈有什么新闻” → 先显示联网状态，再出现带日期的可点击来源。
4. **失败降级**：不配置/填错搜索 key → 明确提示“联网搜索不可用”，聊天继续，不伪造来源。
5. **Agent 工具（P2）**：算数 + 查文件/网页的组合任务 → 工具状态、步数、`tool_runs` 日志。
6. **记忆（P3）**：开启记忆后记住偏好；关闭/删除后不再注入。

## 已知限制

- **文件 RAG 是关键词检索**：中文二元组 + 英文/代码词 + IDF/标题路径，同义改写/跨语言召回仍有限；扫描版 PDF/图片不 OCR。
- **搜索正文依赖 Tavily raw content**：SerpApi 备源仍只有摘要；宽泛新闻可能只返回单一媒体，模型必须明确说明来源不足；没有自建页面抓取器/SSRF 面。
- **图片上传暂不支持**（415）；图像理解与语音在 P4。
- **长期记忆、创作模式、Agent 工具仍在计划中（P2/P3）**：`mode` 字段目前只校验、不改变行为；`tools/` 是 P2 占位。
- **旧上传不会自动重索引**：解析/切块改进只影响新上传；旧文件需重新上传（或后续用重解析脚本）。
- **没有登录认证**：固定本地开发用户，所有查询按 `user_id` 过滤，但不等同于多用户安全隔离；公开部署前必须补认证、限流、HTTPS。
- **前端限制**：只加载最近 100 条消息/50 个会话，没有分页加载控件；缺组件/E2E 测试与 CI。
- **上下文用字符数近似 token**：替换点在 `services/context_builder.py`。
- **重新生成是硬删除**旧的助手消息，不保留历史版本。
- **mock provider 回答固定**，只用于离线开发和测试。

## 开发工作流（VSCode + Claude Code）

1. 先写功能/工程/真实评测三类验收，再进入 Claude Code 的 plan mode。
2. 一次做一个端到端可运行的垂直切片（API + service + 前端 + 测试）。
3. 测试分层：纯函数单测 → MySQL 集成测试 → 真实数据评测。
4. 小步提交；状态/API/表结构变化同步到 `docs/`。
5. 密钥只放 `backend/.env`，不粘贴到聊天、不写进前端、不提交。
6. 详细约束见 `docs/PRD-AND-TECHNICAL-PLAN.md` 第 9 节。

## 相关文档

- `docs/PRD-AND-TECHNICAL-PLAN.md` —— 产品定位、范围、架构、Agent 能力、质量与开发工作流
- `docs/DEVELOPMENT-ROADMAP.md` —— P0–P7 阶段、验收、评测门禁、作品产出
- `docs/API-SPEC.md` —— 接口契约（含 SSE 事件格式）
- `docs/DB-SCHEMA.md` —— 数据库表结构

> 说明：`API-SPEC.md` / `DB-SCHEMA.md` 中仍有部分“计划中”的记忆/设置/工具接口与表结构，尚未挂载或迁移；状态标记将在后续独立任务中同步。
