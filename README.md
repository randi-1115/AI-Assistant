# AI Assistant V1 — 项目骨架

这是一个**第一版项目目录骨架**，根据当前已确认需求创建。它主要提供目录、模块职责、配置模板和文档入口；聊天、模型适配器、搜索服务和 RAG 等业务逻辑尚未实现，不能把它当成已完成的软件直接运行。

## 技术路线

- 前端：Vue 3 + TypeScript + Vite
- 后端：Python + FastAPI
- 数据库：MySQL（建议 SQLAlchemy 2.x + Alembic；异步驱动暂定 `asyncmy`，可在实施时验证）
- 模型：暂不绑定某一家供应商，通过统一接口接入；选择供应商后再编写具体 adapter
- 搜索：仅定义接口，供应商尚未确定
- 文件：第一阶段支持 PDF、DOCX、TXT、Markdown、常见代码文本文件和图片；扫描版 PDF/OCR 暂不保证

## 推荐启动顺序

1. 阅读 `docs/PRD-AND-TECHNICAL-PLAN.md`，确认功能边界。
2. 阅读 `docs/DEVELOPMENT-ROADMAP.md`，按阶段开发。
3. 阅读 `docs/API-SPEC.md` 和 `docs/DB-SCHEMA.md`，先定数据契约。
4. 创建本地 MySQL 数据库，然后实现后端 health endpoint 和数据库连接。
5. 实现 Vue 页面骨架，最后打通流式聊天。
6. 确认模型 API 与搜索服务后，再实现相应适配器。

## 环境变量

复制 `backend/.env.example` 为 `backend/.env`，填入本地数据库与后续选定的服务配置。不要提交 `.env`，也不要把密钥放进前端环境变量。`VITE_` 前缀的值会暴露给浏览器，因此绝不能放服务商密钥。

## 当前尚未选定的服务

- LLM API 服务商、模型、API 兼容格式以及视觉/工具调用能力
- Web Search 服务商
- 云服务器、域名、对象存储
- 后续语义检索是否采用 embedding API 与向量数据库

这些服务在选定前只能通过接口预留，不假设你已开通账号或密钥。

## 项目目录

```text
ai-assistant-v1/
├── docs/                  # 产品需求、接口、数据库、路线图
├── backend/               # FastAPI 后端
├── frontend/              # Vue 前端
├── infra/                 # 可选的本地部署辅助文件
├── data/uploads/           # 本地开发上传文件，不提交 Git
├── .gitignore
└── README.md
```
