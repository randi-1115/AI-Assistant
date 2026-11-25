# P2 Agent 工具评测记录

## 环境与方法

- 日期：2026-11-23
- 模型：`LLM_PROVIDER=deepseek`，`LLM_MODEL=deepseek-flash`（真实 key）
- 搜索：`SEARCH_PROVIDER=tavily`，`SEARCH_FALLBACK_PROVIDER=serpapi`
- 机制类用例用 `LLM_PROVIDER=mock` + `__mock_tool__` 标记，确定性复现多步/失败；其余用真实模型与真实搜索。
- 每个用例独立会话，评测后删除；报告在 `backend/data/eval/p2-{mock,real}.json`（不入库）。

```bash
cd backend
.venv/Scripts/python.exe scripts/eval_p2.py --mode mock --output data/eval/p2-mock.json
.venv/Scripts/python.exe scripts/eval_p2.py --mode real --output data/eval/p2-real.json
```

## 结果

### Mock（机制）2/2

| ID | 场景 | 结果 | 耗时 |
|---|---|---|---|
| M1 | 两行 calculator 标记，多步 | PASS | 77ms |
| M2 | calculator 非法参数 | PASS | 47ms |

### Real（真实模型/搜索）9/9

| ID | 场景 | 工具 | 来源 | 结果 | 耗时 |
|---|---|---|---|---|---|
| R1 | 12345 × 6789 | calculator | 0 | PASS | 3.5s |
| R2 | (3.5+4.5)×7 | calculator | 0 | PASS | 4.2s |
| R3 | 文件事实（代号/负责人） | document_search | 1 | PASS | 4.0s |
| R4 | 文件总结 | document_search ×2 | 1（去重后） | PASS | 6.7s |
| R5 | 最近一周 AI 新闻 | web_search ×3 | 5 | PASS | 41.3s |
| R6 | 2026 iPhone 价格 | web_search ×3 | 10 | PASS | 31.6s |
| R7 | 文件单价×数量（组合） | document_search + calculator | 1 | PASS | 6.3s |
| R8 | 文件预算÷期数（组合） | document_search + calculator | 1 | PASS | 6.1s |
| R9 | 新闻问题，search off | 无 | 0 | PASS | 3.6s |

覆盖：该用工具（R1–R8）、不该用（R9）、多步（M1、R7/R8）、失败回填（M2）；算数/文件/网页/组合各 ≥2 个真实场景。

## 观察

- **R4 调用了两次 document_search，但同名片段只产生 1 条来源**：来源去重与全局编号生效。
- **R5/R6 各有 3 次实际搜索 + 1 次被预算拒绝的调用**：
  - 本网络环境下 Tavily 单次延迟波动较大，一轮里可能有 1–2 次在 12s 搜索预算内超时；
  - 只要至少一次返回来源，评测按"工具被调用 + 有关键事实"判为通过；
  - 最终回答会带"已达到工具调用次数或时间限制"的说明。
- **`tool_budget_exceeded`** 表示"模型还想要工具但本轮预算已用尽"，该调用没有执行，不计入工具执行次数。
- **web 用例耗时明显高于其他场景**（31–41s）：主要来自模型在搜索降级后的重试，不是工具本身卡死；单次搜索通常 2–10s。
- R9 在 `search_mode=off` 时没有调用任何工具，符合"联网需要用户显式同意"。

## 已知边界

- 真实评测是非确定性的；上表是一次真实运行的结果，不保证每次完全一致。
- 工具消息不写入 `messages`，后续轮次不重放工具上下文；只能通过最终回答、`metadata_json` 与 `tool_runs` 追溯。
- `AGENT_MAX_TOOL_CALLS` 是总执行次数，不是步数；达到上限会追加一次无工具收口调用。
- 网页内容与文件片段都是不可信数据，只作为参考。

## 回归策略

改动工具、提示词、搜索预算或 agent 循环后，至少重跑：

```bash
.venv/Scripts/python.exe scripts/eval_p2.py --mode mock
.venv/Scripts/python.exe scripts/eval_p2.py --mode real
```

mock 必须全过；real 的 web 用例允许在搜索确实降级时 skip，但必须记录原因，不能伪造通过。
