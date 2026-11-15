"""P1.5 质量评测脚本。

用法（在 backend/ 下）：
    python scripts/eval_p15.py --mode offline
    python scripts/eval_p15.py --mode search --real
    python scripts/eval_p15.py --mode e2e --real --max-cases 3
    python scripts/eval_p15.py --mode all --real

说明：
- offline：纯解析/切块/检索，不联网、不调用 LLM。
- search：真实 Tavily/SerpApi，不调用 LLM。
- e2e：真实 API + MySQL + 搜索 + LLM（会产生费用），默认清理测试会话。
- 默认 pytest 不运行本脚本；这是手工评测工具。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import get_settings  # noqa: E402
from app.rag.chunker import chunk_document  # noqa: E402
from app.rag.parser_base import ParsedDocument  # noqa: E402
from app.rag.parsers import parse_document  # noqa: E402
from app.rag.pipeline import build_excerpts  # noqa: E402
from app.rag.retriever import (  # noqa: E402
    CandidateChunk,
    is_summary_query,
    query_terms,
    rank_chunks,
    select_baseline_chunks,
)
from app.search.dates import parse_published_date, today_local  # noqa: E402
from app.search.service import SearchService  # noqa: E402
from tests.fixtures.documents import generate_documents  # noqa: E402

DEFAULT_CASES = ROOT / "tests" / "fixtures" / "eval"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


def parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        name = "message"
        data: dict[str, Any] = {}
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                try:
                    data = json.loads(line[len("data:") :].strip())
                except ValueError:
                    data = {}
        if data or name != "message":
            events.append((name, data))
    return events


def _file_retrieval(path: Path, question: str, expected: list[str]) -> dict[str, Any]:
    document = parse_document(
        path,
        extension=path.suffix.lower(),
        max_chars=1_000_000,
    )
    chunks = chunk_document(
        document,
        chunk_size=800,
        overlap=100,
        max_chunks=500,
    )
    candidates = [
        CandidateChunk(
            attachment_id=1,
            file_name=path.name,
            chunk_index=chunk.index,
            section_title=chunk.section_title,
            content=chunk.content,
            heading_path=tuple(chunk.metadata.get("heading_path") or ()),
            kind=str(chunk.metadata.get("kind") or "text"),
        )
        for chunk in chunks
    ]
    terms = query_terms(question)
    ranked = rank_chunks(question, candidates, top_k=4, per_file_k=2)
    if is_summary_query(question) and not ranked:
        ranked = select_baseline_chunks(candidates, top_k=4)
    excerpts = build_excerpts(
        ranked,
        terms=terms,
        max_chars=4000,
        per_file_max_excerpts=3,
    )
    text = "\n".join(excerpt.text for excerpt in excerpts)
    return {
        "parse_ok": True,
        "chunks": len(chunks),
        "excerpts": len(excerpts),
        "keyword_hit": any(keyword in text for keyword in expected),
        "top_section": excerpts[0].chunk.section_title if excerpts else None,
    }


def run_offline(cases_dir: Path, documents: dict[str, Path]) -> list[dict[str, Any]]:
    cases = load_jsonl(cases_dir / "file_cases.jsonl")
    results: list[dict[str, Any]] = []
    for case in cases:
        path = documents[case["doc"]]
        result = _file_retrieval(path, case["question"], case.get("expected_keywords", []))
        result.update({"doc": case["doc"], "question": case["question"]})
        results.append(result)
    return results


def _search_result_summary(outcome: Any, cases: dict[str, Any]) -> dict[str, Any]:
    today = today_local()
    domains = [item.domain for item in outcome.results if item.domain]
    dates = [
        parse_published_date(item.published_on or item.published_date, now=today)
        for item in outcome.results
    ]
    fresh_days = cases.get("fresh_days")
    known_dates = [value for value in dates if value is not None]
    fresh = [
        value
        for value in known_dates
        if fresh_days is None or (today - value).days <= int(fresh_days)
    ]
    expect = cases
    date_expected = expect.get("date")
    intent_ok = expect.get("intent") is None or outcome.intent == expect["intent"]
    fresh_ok = fresh_days is None or not known_dates or len(fresh) >= 1
    return {
        "status": outcome.status,
        "intent": outcome.intent,
        "provider": outcome.provider,
        "calls": outcome.calls,
        "latency_ms": outcome.latency_ms,
        "sources": len(outcome.results),
        "domains": len(set(domains)),
        "domain_list": sorted(set(domains)),
        "date_known": len(known_dates),
        "fresh_dates": len(fresh),
        "date_match": any(item.published_on == date_expected for item in outcome.results)
        if date_expected
        else None,
        "warned": "延迟" in (outcome.message or ""),
        "passed": (
            outcome.status == "ok"
            and intent_ok
            and (expect.get("min_domains") is None or len(set(domains)) >= int(expect["min_domains"]))
            and (date_expected is None or any(item.published_on == date_expected for item in outcome.results))
            and fresh_ok
            and (not expect.get("must_warn") or "延迟" in (outcome.message or ""))
        ),
    }


async def run_search(cases_dir: Path, real: bool) -> list[dict[str, Any]]:
    if not real:
        return [{"skipped": "search 需要 --real（会消耗搜索额度）"}]
    settings = get_settings()
    if settings.search_provider == "none":
        return [{"skipped": "SEARCH_PROVIDER=none，未配置搜索服务"}]
    service = SearchService(settings)
    results: list[dict[str, Any]] = []
    for case in load_jsonl(cases_dir / "search_cases.jsonl"):
        outcome = await service.search(case["query"], mode="auto")
        summary = _search_result_summary(outcome, case.get("expect", {}))
        summary["query"] = case["query"]
        results.append(summary)
    return results


async def _upload(
    client,
    path: Path,
    conversation_id: int,
) -> int | None:
    files = {"file": (path.name, path.read_bytes(), "application/octet-stream")}
    response = await client.post(
        "/api/v1/files/upload",
        files=files,
        data={"conversation_id": str(conversation_id)},
    )
    if response.status_code != 201:
        print(f"  upload failed: {path.name} {response.status_code} {response.text[:200]}")
        return None
    return int(response.json()["file_id"])


async def run_e2e(
    cases_dir: Path,
    documents: dict[str, Path],
    *,
    real: bool,
    keep: bool,
    max_cases: int | None,
) -> list[dict[str, Any]]:
    if not real:
        return [{"skipped": "e2e 需要 --real（会消耗 LLM + 搜索额度）"}]

    from httpx import ASGITransport, AsyncClient

    from app.main import app

    settings = get_settings()
    if settings.llm_provider == "mock":
        print("WARNING: LLM_PROVIDER=mock，E2E 回答是固定文本，不能代表真实质量。")

    cases = load_jsonl(cases_dir / "e2e_cases.jsonl")
    if max_cases is not None:
        cases = cases[:max_cases]

    results: list[dict[str, Any]] = []
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        timeout=180,
    ) as client:
        created = await client.post("/api/v1/conversations", json={})
        if created.status_code != 201:
            return [{"error": f"create conversation failed: {created.text[:200]}"}]
        conversation_id = int(created.json()["id"])
        uploaded: dict[str, int] = {}
        for key, path in documents.items():
            file_id = await _upload(client, path, conversation_id)
            if file_id is not None:
                uploaded[key] = file_id

        for case in cases:
            kind = case.get("kind")
            payload: dict[str, Any] = {
                "content": case["query"],
                "search_mode": "auto" if kind == "search" else "off",
            }
            if kind == "file":
                file_id = uploaded.get(case["doc"])
                if file_id is None:
                    results.append({"kind": kind, "query": case["query"], "error": "upload missing"})
                    continue
                payload["attachment_ids"] = [file_id]

            response = await client.post(
                f"/api/v1/conversations/{conversation_id}/messages/stream",
                json=payload,
            )
            events = parse_sse(response.text)
            sources = [data for name, data in events if name == "source"]
            tool_statuses = [data for name, data in events if name == "tool_status"]
            answer = "".join(
                data.get("text", "") for name, data in events if name == "token"
            )
            end_payload = next(
                (data for name, data in events if name == "message_end"), {}
            )
            expected = case.get("expected_keywords", [])
            file_sources = [item for item in sources if item.get("kind") == "file"]
            web_sources = [item for item in sources if item.get("kind") == "web"]
            acknowledged = any(
                token in answer
                for token in ("单一", "只有", "无法确认", "资料不足", "来源不足", "不能当作")
            )
            if kind == "file":
                passed = (
                    response.status_code == 200
                    and bool(file_sources)
                    and any(keyword in answer for keyword in expected)
                )
            else:
                domains = {item.get("domain") for item in web_sources if item.get("domain")}
                min_domains = case.get("expect", {}).get("min_domains")
                passed = (
                    response.status_code == 200
                    and bool(web_sources)
                    and (
                        min_domains is None
                        or len(domains) >= int(min_domains)
                        or acknowledged
                    )
                )
            results.append(
                {
                    "kind": kind,
                    "query": case["query"],
                    "status_code": response.status_code,
                    "answer_chars": len(answer),
                    "answer_preview": answer[:300],
                    "sources": sources[:5],
                    "domains": sorted(
                        {item.get("domain") for item in web_sources if item.get("domain")}
                    ),
                    "acknowledged": acknowledged,
                    "tool_statuses": tool_statuses,
                    "finish_reason": end_payload.get("finish_reason"),
                    "usage": end_payload.get("usage"),
                    "passed": passed,
                }
            )

        if not keep:
            await client.delete(f"/api/v1/conversations/{conversation_id}")
    return results


def print_report(title: str, results: list[dict[str, Any]]) -> None:
    print(f"\n== {title} ==")
    for item in results:
        print(json.dumps(item, ensure_ascii=False, default=str))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P1.5 search/file quality evaluation")
    parser.add_argument("--mode", choices=["offline", "search", "e2e", "all"], default="offline")
    parser.add_argument("--real", action="store_true", help="use real search/LLM providers")
    parser.add_argument("--cases-dir", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--docs-dir", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--keep", action="store_true", help="keep the E2E conversation")
    parser.add_argument("--max-cases", type=int, default=None)
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="p15-eval-") as temp:
        if args.docs_dir is not None:
            documents = {
                "generic": args.docs_dir / "generic.docx",
                "heading": args.docs_dir / "heading.docx",
                "table": args.docs_dir / "table.docx",
                "text": args.docs_dir / "notes.txt",
            }
            missing = [str(path) for path in documents.values() if not path.is_file()]
            if missing:
                print("docs-dir 缺少文件：", ", ".join(missing))
                return 2
        else:
            documents = generate_documents(Path(temp))

        report: dict[str, Any] = {"mode": args.mode}
        if args.mode in ("offline", "all"):
            report["offline"] = run_offline(args.cases_dir, documents)
            print_report("offline file retrieval", report["offline"])
        if args.mode in ("search", "all"):
            report["search"] = asyncio.run(run_search(args.cases_dir, args.real))
            print_report("search", report["search"])
        if args.mode in ("e2e", "all"):
            report["e2e"] = asyncio.run(
                run_e2e(
                    args.cases_dir,
                    documents,
                    real=args.real,
                    keep=args.keep,
                    max_cases=args.max_cases,
                )
            )
            print_report("e2e", report["e2e"])

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"\nreport written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
