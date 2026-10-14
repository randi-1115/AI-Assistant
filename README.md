# AI Assistant V1

网页版个人 AI 助手，交互参考豆包 / ChatGPT：左侧会话列表、中间消息区、底部输入框。

**当前进度：P0 已完成** —— 会话管理、多轮聊天、流式输出、停止生成、重新生成、Markdown 渲染、MySQL 持久化、统一错误结构与请求日志都能跑通。

P1（联网搜索、文件问答、图片理解、长期记忆、创作模式、Agent 工具）**尚未实现**，目录和数据契约已留好位置。

## 技术路线

| 层 | 选型 |
|---|---|
| 前端 | Vue 3 + TypeScript + Vite（Pinia、Vue Router、markdown-it、highlight.js） |
| 后端 | Python 3.12 + FastAPI + SQLAlchemy 2.x（async） |
| 数据库 | MySQL 8.x + Alembic 迁移 |
| 模型 | DeepSeek（OpenAI 兼容协议）；另有 mock provider 供离线开发与测试 |
| 文件存储 | 本地私有目录（P1 才用） |

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

## 主要环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `DATABASE_URL` | 必填 | MySQL 连接串，驱动用 `aiomysql` |
| `TEST_DATABASE_URL` | 空 | 集成测试库；留空则集成测试整体跳过 |
| `LLM_PROVIDER` | `mock` | `mock` 或 `deepseek` |
| `LLM_API_KEY` / `LLM_MODEL` | 空 | `deepseek` 模式下必填 |
| `CONTEXT_MAX_MESSAGES` | 20 | 发给模型的最近消息条数上限 |
| `CONTEXT_MAX_CHARS` | 8000 | 上下文字符预算（token 的近似值） |
| `MOCK_FORCE_ERROR` | 空 | 强制 mock 报错，用于验收错误分支：`upstream`/`timeout`/`rate_limit`/`auth` |

完整列表见 `backend/.env.example`。

## 测试

```bash
# 后端：单元测试不需要数据库和网络
cd backend
pytest tests/unit

# 后端：集成测试需要 MySQL 测试库（见 TEST_DATABASE_URL）
pytest tests/integration

# 前端：SSE 解析器
cd frontend
npm test
npm run type-check
```

单元测试全部用 mock provider 驱动，**不依赖真实 API，不消耗 token**。

## 项目结构

```text
ai-assistant-v1/
├── docs/                  # 需求、API 契约、数据库表结构、开发路线图
├── backend/
│   ├── alembic/           # 数据库迁移
│   └── app/
│       ├── api/           # HTTP 层：只做参数校验、调服务、返回
│       ├── core/          # 配置、日志、统一错误
│       ├── db/            # 引擎、会话、基类、启动种子
│       ├── models/        # SQLAlchemy ORM
│       ├── repositories/  # SQL 查询（所有查询强制按 user_id 过滤）
│       ├── schemas/       # Pydantic 请求/响应契约
│       ├── services/      # 业务流程：会话、聊天、上下文组装
│       ├── ai/providers/  # LLM 抽象、mock、OpenAI 兼容 adapter
│       ├── rag/ tools/ storage/   # P1 占位
├── frontend/src/
│   ├── api/               # 请求封装、SSE 解析
│   ├── components/        # 布局与会话聊天组件
│   ├── stores/            # Pinia：会话列表、消息与流式状态
│   ├── types/             # 与后端 schema 一一对应的类型
│   ├── views/             # 页面
│   └── utils/             # Markdown 渲染配置
└── data/uploads/          # 本地上传目录（P1）
```

## 已知限制

- **没有登录认证**。第一阶段固定使用一个本地开发用户，所有查询按 `user_id` 过滤，为后续认证留好了位置，但这**不等同于**已完成多用户安全隔离。公开部署前必须补齐认证、限流与 HTTPS。
- **上下文用字符数近似 token**。够用且能避免无限传历史，但不是精确计数；P1 若引入真 tokenizer，替换点在 `services/context_builder.py`。
- **重新生成是硬删除**旧的助手消息，不保留历史版本。
- **创作模式的 `mode` 字段只做校验**，所有取值当前都按通用聊天处理（前端也还没暴露模式选择器）。
- **mock provider 的回答是固定文本**，只为把链路跑通，不具备任何智能。
- 数据库 root 账号仅用于本地开发；上线前应换成最小权限的专用账号。

## 相关文档

- `docs/PRD-AND-TECHNICAL-PLAN.md` —— 产品需求与技术方案
- `docs/API-SPEC.md` —— 接口契约（含 SSE 事件格式）
- `docs/DB-SCHEMA.md` —— 数据库表结构
- `docs/DEVELOPMENT-ROADMAP.md` —— 分阶段开发与验收清单
