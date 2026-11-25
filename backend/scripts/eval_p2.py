"""P2 Agent 工具评测：mock 机制 + 真实模型/搜索。

用法（在 backend/ 下）：
    python scripts/eval_p2.py --mode mock
    python scripts/eval_p2.py --mode real
    python scripts/eval_p2.py --mode all --output data/eval/p2.json

原则：
- mock 模式用显式标记稳定复现多步/失败，不产生外部请求；
- real 模式用 .env 的真实 provider/search，web 用例在搜索不可用时记 skipped；
- 每个用例独立会话，默认删除，不留下评测数据。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.eval_p15 import load_jsonl, parse_sse  # noqa: E402

DEFAULT_CASES = ROOT / "tests" / "fixtures" / "eval" / "p2_cases.jsonl"


def _normalize(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch not in ",，.。 \t\r\n")


def _failures(case: dict[str, Any], observed: dict[str, Any]) -> list[str]:
    expect = case.get("expect", {})
    failures: list[str] = []

    tool_names = observed["tool_names"]
    requested_tools = observed.get("requested_tools", tool_names)
    for name in expect.get("tools", []):
        if name not in tool_names:
            failures.append(f"缺少工具：{name}")
    for name in expect.get("forbidden_tools", []):
        if name in requested_tools:
            failures.append(f"不应调用工具：{name}")

    if "max_tool_calls" in expect and observed["tool_calls"] > int(
        expect["max_tool_calls"]
    ):
        failures.append(
            f"工具调用过多：{observed['tool_calls']} > {expect['max_tool_calls']}"
        )
    if "min_file_sources" in expect and len(observed["file_sources"]) < int(
        expect["min_file_sources"]
    ):
        failures.append("文件来源不足")
    if "min_web_sources" in expect and len(observed["web_sources"]) < int(
        expect["min_web_sources"]
    ):
        failures.append("网页来源不足")
    if expect.get("no_sources") and observed["sources"]:
        failures.append("不应出现来源")
    if "min_answer_chars" in expect and len(observed["answer"].strip()) < int(
        expect["min_answer_chars"]
    ):
        failures.append("回答为空或过短")
    if expect.get("answer_any"):
        answer = _normalize(observed["answer"])
        if not any(_normalize(token) in answer for token in expect["answer_any"]):
            failures.append(f"回答未包含任一关键词：{expect['answer_any']}")
    if "finish_reason" in expect and observed["finish_reason"] != expect["finish_reason"]:
        failures.append(
            f"finish_reason={observed['finish_reason']}，期望 {expect['finish_reason']}"
        )
    return failures


async def _upload(client, conversation_id: int, file: dict[str, Any]) -> int | None:
    response = await client.post(
        "/api/v1/files/upload",
        files={
            "file": (
                file["name"],
                file["content"].encode("utf-8"),
                "text/plain",
            )
        },
        data={"conversation_id": str(conversation_id)},
    )
    if response.status_code != 201:
        return None
    return int(response.json()["file_id"])


async def run_phase(cases: list[dict[str, Any]], mode: str, keep: bool) -> list[dict]:
    from httpx import ASGITransport, AsyncClient

    from app.ai.providers.mock import MockProvider
    from app.ai.providers.registry import llm_provider_dep
    from app.core.config import get_settings
    from app.main import app
    from app.search import get_search_service, search_available
    from app.search.service import SearchService

    settings = get_settings()
    overrides: dict[Any, Any] = {}
    if mode == "mock":
        overrides[llm_provider_dep] = lambda: MockProvider(stream_delay_ms=0)
        overrides[get_search_service] = lambda: SearchService(
            settings, provider=None, resolve_provider=False
        )
    app.dependency_overrides.update(overrides)

    results: list[dict[str, Any]] = []
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            timeout=180,
        ) as client:
            for case in cases:
                if case.get("mode") != mode:
                    continue

                record: dict[str, Any] = {
                    "id": case["id"],
                    "mode": mode,
                    "query": case["query"],
                }

                requires_search = "search" in case.get("requires", [])
                if requires_search and not search_available(settings):
                    record.update(skipped="search_unavailable")
                    results.append(record)
                    continue

                conversation = await client.post(
                    "/api/v1/conversations",
                    json={"title": f"eval-p2-{case['id']}"},
                )
                if conversation.status_code != 201:
                    record.update(passed=False, failures=["创建会话失败"])
                    results.append(record)
                    continue
                conversation_id = int(conversation.json()["id"])

                try:
                    payload: dict[str, Any] = {
                        "content": case["query"],
                        "agent_mode": "auto",
                        "search_mode": case.get("search_mode", "off"),
                    }
                    if case.get("file"):
                        file_id = await _upload(client, conversation_id, case["file"])
                        if file_id is None:
                            record.update(passed=False, failures=["附件上传失败"])
                            results.append(record)
                            continue
                        payload["attachment_ids"] = [file_id]

                    started = time.monotonic()
                    response = await client.post(
                        f"/api/v1/conversations/{conversation_id}/messages/stream",
                        json=payload,
                    )
                    latency_ms = int((time.monotonic() - started) * 1000)
                    events = parse_sse(response.text)
                    terminal = [
                        data
                        for name, data in events
                        if name == "tool_status" and data.get("status") != "running"
                    ]
                    # 预算拒绝的调用没有执行，不算一次工具调用。
                    executed = [
                        item
                        for item in terminal
                        if item.get("error_code") != "tool_budget_exceeded"
                    ]
                    sources = [data for name, data in events if name == "source"]
                    answer = "".join(
                        data.get("text", "") for name, data in events if name == "token"
                    )
                    end_payload = next(
                        (data for name, data in events if name == "message_end"), {}
                    )
                    observed = {
                        "tool_names": [item.get("tool") for item in executed],
                        "requested_tools": [item.get("tool") for item in terminal],
                        "tool_calls": len(executed),
                        "tool_statuses": terminal,
                        "sources": sources,
                        "file_sources": [
                            item for item in sources if item.get("kind") == "file"
                        ],
                        "web_sources": [
                            item for item in sources if item.get("kind") == "web"
                        ],
                        "answer": answer,
                        "finish_reason": end_payload.get("finish_reason"),
                    }
                    record.update(
                        status_code=response.status_code,
                        latency_ms=latency_ms,
                        tool_names=observed["tool_names"],
                        tool_calls=observed["tool_calls"],
                        tool_statuses=terminal,
                        source_count=len(sources),
                        answer=answer[:500],
                        finish_reason=observed["finish_reason"],
                        usage=end_payload.get("usage"),
                    )

                    if response.status_code != 200:
                        record.update(passed=False, failures=["HTTP 非 200"])
                    elif (
                        requires_search
                        and not observed["web_sources"]
                        and any(
                            item.get("tool") == "web_search"
                            and item.get("status") in ("empty", "unavailable", "failed")
                            for item in terminal
                        )
                    ):
                        # 真实搜索确实降级且没有拿到任何来源：记 skip，不算模型选错工具。
                        record.update(skipped="search_degraded")
                    else:
                        failures = _failures(case, observed)
                        record.update(passed=not failures, failures=failures)
                finally:
                    if not keep:
                        await client.delete(f"/api/v1/conversations/{conversation_id}")
                results.append(record)
    finally:
        for key in overrides:
            app.dependency_overrides.pop(key, None)
    return results


def print_report(title: str, results: list[dict[str, Any]]) -> None:
    print(f"\n== {title} ==")
    for item in results:
        if item.get("skipped"):
            print(f"[SKIP] {item['id']}: {item['skipped']}")
        elif item.get("passed"):
            print(f"[PASS] {item['id']} ({item.get('latency_ms')}ms)")
        else:
            print(f"[FAIL] {item['id']}: {item.get('failures')}")
    total = len([item for item in results if not item.get("skipped")])
    passed = len([item for item in results if item.get("passed")])
    skipped = len([item for item in results if item.get("skipped")])
    print(f"passed={passed}/{total} skipped={skipped}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P2 agent/tool evaluation")
    parser.add_argument("--mode", choices=["mock", "real", "all"], default="all")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--max-cases", type=int, default=None)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)

    cases = load_jsonl(args.cases)
    if args.max_cases is not None:
        cases = cases[: args.max_cases]

    report: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": args.mode,
        "cases": [],
    }
    if args.mode in ("mock", "all"):
        report["mock"] = asyncio.run(run_phase(cases, "mock", args.keep))
        print_report("mock", report["mock"])
    if args.mode in ("real", "all"):
        report["real"] = asyncio.run(run_phase(cases, "real", args.keep))
        print_report("real", report["real"])

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"\nreport written to {args.output}")

    failed = [
        item
        for group in ("mock", "real")
        for item in report.get(group, [])
        if not item.get("passed") and not item.get("skipped")
    ]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
