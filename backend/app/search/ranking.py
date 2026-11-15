"""搜索结果去重、日期过滤、权威性与多样性排序。

纯函数，不依赖 provider/DB；便于离线测试。所有判断都是启发式，
目标是让宽泛新闻不被单一热点/聚合站占满，不承诺绝对权威。
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import replace
from datetime import date
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.search.base import SearchResult
from app.search.dates import (
    days_outside_window,
    is_in_window,
    parse_published_date,
    today_local,
)
from app.search.query_planner import QueryPlan

_WHITESPACE = re.compile(r"\s+")
_TITLE_PUNCT = re.compile(r"[\s\-—_:：,，.。!！?？|｜/\\()（）\[\]【】\"'“”‘’]+")
_LISTING_PATH = re.compile(
    r"/(category|tag|topic|search|list|index|channel|news)(/|$)", re.IGNORECASE
)

_TIER1_DOMAINS = frozenset(
    {
        "openai.com",
        "anthropic.com",
        "deepmind.google",
        "ai.google",
        "blog.google",
        "microsoft.com",
        "nvidia.com",
        "deepseek.com",
        "qwen.ai",
        "meta.com",
        "about.fb.com",
        "reuters.com",
        "bloomberg.com",
        "apnews.com",
        "bbc.com",
        "xinhuanet.com",
        "news.cn",
        "people.com.cn",
        "thepaper.cn",
        "caixin.com",
        "arxiv.org",
        "nature.com",
        "science.org",
    }
)
_TIER2_DOMAINS = frozenset(
    {
        "36kr.com",
        "jiqizhixin.com",
        "qbitai.com",
        "huxiu.com",
        "sina.com.cn",
        "ifeng.com",
        "cnbeta.com",
        "techcrunch.com",
        "theverge.com",
        "wired.com",
        "ithome.com",
        "infoq.cn",
        "tmtpost.com",
    }
)
_TIER34_DOMAINS = frozenset(
    {
        "zhihu.com",
        "baijiahao.baidu.com",
        "sohu.com",
        "toutiao.com",
        "csdn.net",
        "jianshu.com",
        "weibo.com",
        "x.com",
        "twitter.com",
        "reddit.com",
        "quora.com",
        "pinterest.com",
        "facebook.com",
        "linkedin.com",
        "apps.apple.com",
        "play.google.com",
    }
)
_AUTHORITY_SCORE = {1: 1.0, 2: 0.75, 3: 0.4, 4: 0.2}
_ASCII_WORD = re.compile(r"[a-z0-9_]{2,}")
_ACTION_TERMS = ("发布", "推出", "更新", "上线", "融资", "收购")
_TOPIC_TERMS = {
    "news": (
        "ai",
        "人工智能",
        "大模型",
        "模型",
        "openai",
        "anthropic",
        "deepseek",
        "谷歌",
        "英伟达",
        "芯片",
        "机器人",
        "融资",
        "发布",
    ),
    "entity_update": ("模型", "发布", "推出", "更新", "上线", "融资", "收购"),
    "realtime_data": ("股价", "股票", "行情", "汇率", "天气", "价格", "实时"),
}


def domain_of(url: str) -> str:
    try:
        hostname = (urlsplit(url).hostname or "").lower()
    except (ValueError, UnicodeError):
        return ""
    if hostname.startswith("www."):
        hostname = hostname[4:]
    return hostname


def canonical_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
        port = parts.port
    except (ValueError, UnicodeError):
        return url
    if not hostname:
        return url
    host = hostname.lower()
    if host.startswith("www."):
        host = host[4:]
    default_port = (parts.scheme == "http" and port == 80) or (
        parts.scheme == "https" and port == 443
    )
    netloc = host if not port or default_port else f"{host}:{port}"
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
            and key.lower() not in {"gclid", "fbclid", "msclkid"}
        )
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), netloc, path, query, ""))


def title_key(title: str) -> str:
    normalized = unicodedata.normalize("NFKC", title or "").casefold()
    return _TITLE_PUNCT.sub("", normalized)[:160]


def is_listing_page(title: str, url: str, snippet: str) -> bool:
    try:
        path = urlsplit(url).path.rstrip("/")
    except (ValueError, UnicodeError):
        path = ""
    if _LISTING_PATH.search(path):
        return True
    clean_title = (title or "").strip().casefold()
    if len(clean_title) < 4:
        return True
    if clean_title.endswith(("首页", "频道", "列表", "专题", "更多", "大全", "home", "index")):
        return True
    return False


def _domain_matches(domain: str, known: str) -> bool:
    return domain == known or domain.endswith("." + known)


def authority_tier(
    domain: str,
    *,
    extra_domains: frozenset[str] = frozenset(),
) -> int:
    domain = domain.lower().strip()
    if not domain:
        return 3
    if domain.endswith((".gov", ".gov.cn", ".edu", ".edu.cn")):
        return 1
    if any(_domain_matches(domain, item) for item in extra_domains | _TIER1_DOMAINS):
        return 1
    if any(_domain_matches(domain, item) for item in _TIER2_DOMAINS):
        return 2
    if any(_domain_matches(domain, item) for item in _TIER34_DOMAINS):
        return 4
    return 2


def _topical_terms(plan: QueryPlan) -> tuple[str, ...]:
    lower = plan.search_query.casefold()
    terms = list(_ASCII_WORD.findall(lower))
    terms.extend(_TOPIC_TERMS.get(plan.intent, ()))
    seen: set[str] = set()
    selected: list[str] = []
    for term in terms:
        if not term or term in seen:
            continue
        seen.add(term)
        selected.append(term)
        if len(selected) >= 12:
            break
    return tuple(selected)


def _relevance_and_title_hits(
    result: SearchResult,
    terms: tuple[str, ...],
    plan: QueryPlan,
) -> tuple[float, int]:
    if not terms:
        return 0.5, 1
    title = result.title.casefold()
    snippet = result.snippet.casefold()
    title_hits = sum(1 for term in terms if term in title)
    snippet_hits = sum(1 for term in terms if term in snippet)
    if plan.intent in ("news", "entity_update") and title_hits == 0:
        # 标题完全不沾主题，通常是宏观/观点/导航页混入新闻结果。
        return 0.0, 0
    relevance = min(
        1.0,
        0.70 * min(1.0, title_hits / 2)
        + 0.30 * min(1.0, snippet_hits / 3),
    )
    return relevance, title_hits


def freshness_score(published: date | None, plan: QueryPlan, *, now: date) -> float:
    if published is None:
        return 0.35
    if plan.date_window is None:
        return 0.5
    return 1.0 if is_in_window(published, plan.date_window) else 0.15


def dedupe_results(
    results: list[SearchResult],
    *,
    limit: int | None = None,
) -> list[SearchResult]:
    seen_urls: set[str] = set()
    seen_titles: set[tuple[str, str]] = set()
    deduped: list[SearchResult] = []
    for result in results:
        url_key = canonical_url(result.url)
        domain = domain_of(result.url)
        title = title_key(result.title)
        if url_key in seen_urls or (title, domain) in seen_titles:
            continue
        seen_urls.add(url_key)
        seen_titles.add((title, domain))
        deduped.append(result)
        if limit is not None and len(deduped) >= limit:
            break
    return deduped


def postprocess_results(
    results: list[SearchResult],
    *,
    plan: QueryPlan,
    final_limit: int,
    per_domain_cap: int,
    extra_authority_domains: frozenset[str] = frozenset(),
    now: date | None = None,
) -> list[SearchResult]:
    current = now or today_local()
    deduped = dedupe_results(results)
    if not deduped:
        return []

    scored: list[tuple[float, SearchResult, bool]] = []
    topical_terms = _topical_terms(plan)
    for index, result in enumerate(deduped):
        domain = result.domain or domain_of(result.url)
        published = parse_published_date(result.published_date, now=current)
        tier = result.authority_tier or authority_tier(
            domain, extra_domains=extra_authority_domains
        )
        listing = is_listing_page(result.title, result.url, result.snippet)
        authority = _AUTHORITY_SCORE.get(tier, 0.6)
        freshness = freshness_score(published, plan, now=current)
        in_window = 1.0 if (
            published is None or plan.date_window is None or is_in_window(published, plan.date_window)
        ) else 0.0
        provider_score = result.provider_score if result.provider_score is not None else 0.5
        provider_score = min(1.0, max(0.0, provider_score))
        relevance, title_hits = _relevance_and_title_hits(result, topical_terms, plan)
        action_boost = (
            0.15
            if plan.intent == "entity_update"
            and any(term in result.title for term in _ACTION_TERMS)
            else 0.0
        )
        offtopic_penalty = (
            0.25
            if plan.intent in ("news", "entity_update") and title_hits == 0
            else 0.0
        )
        score = (
            0.40 * authority
            + 0.25 * freshness
            + 0.15 * in_window
            + 0.10 * provider_score
            + 0.10 * relevance
            + action_boost
            - (0.35 if listing else 0.0)
            - offtopic_penalty
        )

        drop = False
        if (
            plan.freshness_required
            and plan.date_window is not None
            and published is not None
            and not is_in_window(published, plan.date_window)
        ):
            window_days = max(1, (plan.date_window.end - plan.date_window.start).days + 1)
            if days_outside_window(published, plan.date_window) > max(3 * window_days, 30):
                drop = True

        enriched = replace(
            result,
            domain=domain,
            published_on=published.isoformat() if published else None,
            authority_tier=tier,
            provider_rank=result.provider_rank or index,
            score=round(score, 4),
        )
        scored.append((score, enriched, drop))

    scored.sort(key=lambda item: (-item[0], item[1].domain, title_key(item[1].title)))

    selected: list[SearchResult] = []
    domain_counts: Counter[str] = Counter()
    listing_count = 0
    for score, result, drop in scored:
        if drop:
            continue
        if domain_counts[result.domain] >= per_domain_cap:
            continue
        if is_listing_page(result.title, result.url, result.snippet):
            if listing_count >= 1:
                continue
            listing_count += 1
        selected.append(result)
        domain_counts[result.domain] += 1
        if len(selected) >= final_limit:
            return selected

    # 多样性筛选后不足时，从被域名上限挡住的结果里回填。
    if len(selected) < final_limit:
        selected_ids = {id(result) for result in selected}
        for _, result, drop in scored:
            if drop or id(result) in selected_ids:
                continue
            selected.append(result)
            if len(selected) >= final_limit:
                break
    return selected[:final_limit]
