"""日期窗口与发布时间解析。

搜索结果的日期格式非常杂（RFC 2822、ISO、中文、`11/12/2026`），
查询里的时间表达也很多（今天/最近一周/2026年11月/过去90天）。
这里集中做两件事：把查询解析成 DateWindow，把结果日期解析成 date。
所有函数失败都返回 None，不抛异常。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Literal

DateWindowKind = Literal["relative", "calendar", "explicit"]


@dataclass(frozen=True, slots=True)
class DateWindow:
    start: date
    end: date
    label: str
    kind: DateWindowKind


def today_local() -> date:
    return date.today()


def _window(start: date, end: date, label: str, kind: DateWindowKind) -> DateWindow:
    return DateWindow(start=start, end=end, label=label, kind=kind)


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _previous_month(value: date) -> tuple[date, date]:
    first = _month_start(value)
    end = first - timedelta(days=1)
    return end.replace(day=1), end


def _parse_date_parts(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_published_date(raw: str | None, *, now: date | None = None) -> date | None:
    """把 provider 返回的发布日期解析成 date；解析不了返回 None。"""
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    current = now or today_local()

    # RFC 2822: Tue, 10 Nov 2026 01:00:00 GMT
    try:
        return parsedate_to_datetime(text).date()
    except (TypeError, ValueError, OverflowError):
        pass

    # ISO 及常见分隔符
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    # 11/12/2026：默认 MM/DD/YYYY；首段 > 12 时按 DD/MM/YYYY
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if match:
        first, second, year = (int(part) for part in match.groups())
        if first > 12 and second <= 12:
            month, day = second, first
        else:
            month, day = first, second
        return _parse_date_parts(year, month, day)

    # 11月12日 / 2026年11月12日（后者已被上面的格式覆盖）
    match = re.search(r"(\d{1,2})月(\d{1,2})日", text)
    if match:
        month, day = (int(part) for part in match.groups())
        candidate = _parse_date_parts(current.year, month, day)
        if candidate is not None and candidate > current + timedelta(days=1):
            candidate = _parse_date_parts(current.year - 1, month, day)
        return candidate

    # 相对日期
    if "今天" in text or "刚刚" in text or "today" in text.casefold():
        return current
    if "昨天" in text or "yesterday" in text.casefold():
        return current - timedelta(days=1)

    match = re.search(r"(\d+)\s*(天|日|days?)\s*(前|ago)", text, re.IGNORECASE)
    if match:
        return current - timedelta(days=int(match.group(1)))
    if re.search(r"(\d+)\s*(小时|hours?|分钟|minutes?)\s*(前|ago)", text, re.IGNORECASE):
        return current

    return None


def resolve_explicit_date_window(
    query: str, *, now: date | None = None
) -> DateWindow | None:
    """从查询里解析相对/绝对时间窗；没有明确时间语义返回 None。"""
    current = now or today_local()
    text = query.strip()
    lower = text.casefold()

    # 精确日期：2026-11-12 / 2026/11/12 / 2026年11月12日 / 11月12日
    match = re.search(
        r"(?P<y>\d{4})\s*[-/.年]\s*(?P<m>\d{1,2})\s*[-/.月]\s*(?P<d>\d{1,2})\s*日?",
        text,
    )
    if match:
        value = _parse_date_parts(
            int(match.group("y")), int(match.group("m")), int(match.group("d"))
        )
        if value is not None:
            return _window(value, value, value.isoformat(), "explicit")

    match = re.search(r"(?<!\d)(?P<m>\d{1,2})月(?P<d>\d{1,2})日", text)
    if match:
        month, day = int(match.group("m")), int(match.group("d"))
        value = _parse_date_parts(current.year, month, day)
        if value is not None and value > current + timedelta(days=1):
            value = _parse_date_parts(current.year - 1, month, day)
        if value is not None:
            return _window(value, value, value.isoformat(), "explicit")

    # 年月：2026年11月 / 2026-11
    match = re.search(r"(?P<y>\d{4})\s*[-/.年]\s*(?P<m>\d{1,2})\s*月?", text)
    if match and not re.search(r"\d{1,2}\s*日", match.group(0)):
        year, month = int(match.group("y")), int(match.group("m"))
        start = _parse_date_parts(year, month, 1)
        if start is not None:
            next_month = _parse_date_parts(
                year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 1
            )
            end = (next_month - timedelta(days=1)) if next_month else start
            return _window(start, end, f"{year}-{month:02d}", "explicit")

    # 相对/日历语义：先匹配更长的短语
    rules: tuple[tuple[tuple[str, ...], str], ...] = (
        (("最近一周", "近一周", "过去一周", "最近7天", "过去7天", "近7天", "this week"), "last7"),
        (("最近一个月", "近一个月", "过去一个月", "最近30天", "过去30天", "this month"), "last30"),
        (("最近三个月", "近三个月", "过去90天", "近90天", "最近90天"), "last90"),
        (("本周末", "本星期", "本周", "这周"), "this_week"),
        (("上周",), "last_week"),
        (("本月", "这个月"), "this_month"),
        (("上个月",), "last_month"),
        (("今年", "this year"), "this_year"),
        (("去年", "last year"), "last_year"),
        (("今天", "今日", "今天", "刚刚", "today"), "today"),
        (("昨天", "yesterday"), "yesterday"),
    )
    for phrases, kind in rules:
        if any(phrase in lower for phrase in phrases):
            return _relative_window(kind, current)

    if any(term in lower for term in ("最近", "近期", "最新", "这几天", "目前", "recent", "latest")):
        # 裸“最近”只给一个宽窗口；具体 intent 由 planner 决定是否使用。
        return _window(
            current - timedelta(days=29),
            current,
            "最近30天",
            "relative",
        )

    return None


def _relative_window(kind: str, current: date) -> DateWindow:
    if kind == "today":
        return _window(current, current, "今天", "relative")
    if kind == "yesterday":
        value = current - timedelta(days=1)
        return _window(value, value, "昨天", "relative")
    if kind == "last7":
        return _window(current - timedelta(days=6), current, "最近7天", "relative")
    if kind == "last30":
        return _window(current - timedelta(days=29), current, "最近30天", "relative")
    if kind == "last90":
        return _window(current - timedelta(days=89), current, "最近90天", "relative")
    if kind == "this_week":
        start = current - timedelta(days=current.weekday())
        return _window(start, current, "本周", "calendar")
    if kind == "last_week":
        this_start = current - timedelta(days=current.weekday())
        end = this_start - timedelta(days=1)
        return _window(end - timedelta(days=6), end, "上周", "calendar")
    if kind == "this_month":
        return _window(_month_start(current), current, "本月", "calendar")
    if kind == "last_month":
        start, end = _previous_month(current)
        return _window(start, end, "上个月", "calendar")
    if kind == "this_year":
        return _window(date(current.year, 1, 1), current, "今年", "calendar")
    if kind == "last_year":
        return _window(date(current.year - 1, 1, 1), date(current.year - 1, 12, 31), "去年", "calendar")
    return _window(current, current, "今天", "relative")


def window_for_time_range(time_range: str | None, *, now: date | None = None) -> DateWindow | None:
    """把 LLM/配置给出的 day/week/month/year 映射成相对窗口。"""
    if not time_range:
        return None
    current = now or today_local()
    mapping = {
        "day": ("今天", "today"),
        "week": ("最近7天", "last7"),
        "month": ("最近30天", "last30"),
        "year": ("今年", "this_year"),
    }
    item = mapping.get(time_range)
    return _relative_window(item[1], current) if item else None


def provider_time_range_for(
    window: DateWindow | None, *, now: date | None = None
) -> str | None:
    """选择能覆盖该窗口的最小 provider 时间范围；绝对历史窗口返回 None。"""
    if window is None:
        return None
    current = now or today_local()
    if window.end != current:
        return None
    days = (current - window.start).days
    if days <= 0:
        return "day"
    if days <= 6:
        return "week"
    if days <= 30:
        return "month"
    if window.start == date(current.year, 1, 1) or days <= 365:
        return "year"
    return None


def days_outside_window(value: date, window: DateWindow) -> int:
    if value < window.start:
        return (window.start - value).days
    if value > window.end:
        return (value - window.end).days
    return 0


def is_in_window(value: date | None, window: DateWindow | None) -> bool:
    if value is None or window is None:
        return True
    return window.start <= value <= window.end
