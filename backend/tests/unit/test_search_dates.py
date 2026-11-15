"""日期解析与查询时间窗。"""

from __future__ import annotations

from datetime import date, timedelta

from app.search.dates import (
    days_outside_window,
    is_in_window,
    parse_published_date,
    provider_time_range_for,
    resolve_explicit_date_window,
    window_for_time_range,
)

NOW = date(2026, 11, 15)


def test_parse_rfc2822_date() -> None:
    assert parse_published_date("Tue, 10 Nov 2026 01:00:00 GMT", now=NOW) == date(2026, 11, 10)


def test_parse_iso_and_chinese_dates() -> None:
    assert parse_published_date("2026-11-12", now=NOW) == date(2026, 11, 12)
    assert parse_published_date("2026/11/12", now=NOW) == date(2026, 11, 12)
    assert parse_published_date("2026年11月12日", now=NOW) == date(2026, 11, 12)
    assert parse_published_date("11月12日", now=NOW) == date(2026, 11, 12)


def test_parse_numeric_ambiguous_defaults_to_month_first() -> None:
    assert parse_published_date("11/12/2026", now=NOW) == date(2026, 11, 12)
    # 首段大于 12 时按 DD/MM/YYYY
    assert parse_published_date("25/12/2026", now=NOW) == date(2026, 12, 25)


def test_parse_relative_and_invalid() -> None:
    assert parse_published_date("昨天", now=NOW) == date(2026, 11, 14)
    assert parse_published_date("2 days ago", now=NOW) == date(2026, 11, 13)
    assert parse_published_date("3小时前", now=NOW) == NOW
    assert parse_published_date("not-a-date", now=NOW) is None
    assert parse_published_date(None, now=NOW) is None


def test_explicit_date_window() -> None:
    window = resolve_explicit_date_window("2026年11月12日 AI 新闻", now=NOW)
    assert window is not None
    assert window.start == window.end == date(2026, 11, 12)

    month = resolve_explicit_date_window("2026年11月 AI 新闻", now=NOW)
    assert month is not None
    assert month.start == date(2026, 11, 1)
    assert month.end == date(2026, 11, 30)

    day_without_year = resolve_explicit_date_window("11月12日 AI 新闻", now=NOW)
    assert day_without_year is not None
    assert day_without_year.start == date(2026, 11, 12)


def test_relative_windows() -> None:
    today = resolve_explicit_date_window("今天有什么新闻", now=NOW)
    assert today is not None and today.start == today.end == NOW

    yesterday = resolve_explicit_date_window("昨天发生了什么", now=NOW)
    assert yesterday is not None and yesterday.start == NOW - timedelta(days=1)

    last7 = resolve_explicit_date_window("最近一周 AI 新闻", now=NOW)
    assert last7 is not None
    assert last7.start == NOW - timedelta(days=6) and last7.end == NOW

    last30 = resolve_explicit_date_window("最近一个月 AI 新闻", now=NOW)
    assert last30 is not None
    assert last30.start == NOW - timedelta(days=29)

    last90 = resolve_explicit_date_window("过去90天 AI 新闻", now=NOW)
    assert last90 is not None
    assert last90.start == NOW - timedelta(days=89)

    this_month = resolve_explicit_date_window("本月 AI 新闻", now=NOW)
    assert this_month is not None
    assert this_month.start == date(2026, 11, 1)

    last_month = resolve_explicit_date_window("上个月 AI 新闻", now=NOW)
    assert last_month is not None
    assert last_month.start == date(2026, 10, 1)
    assert last_month.end == date(2026, 10, 31)

    this_year = resolve_explicit_date_window("今年 AI 新闻", now=NOW)
    assert this_year is not None
    assert this_year.start == date(2026, 1, 1)

    last_year = resolve_explicit_date_window("去年 AI 新闻", now=NOW)
    assert last_year is not None
    assert last_year.start == date(2025, 1, 1)
    assert last_year.end == date(2025, 12, 31)


def test_provider_time_range_mapping() -> None:
    assert provider_time_range_for(resolve_explicit_date_window("今天", now=NOW), now=NOW) == "day"
    assert provider_time_range_for(resolve_explicit_date_window("最近一周", now=NOW), now=NOW) == "week"
    assert provider_time_range_for(resolve_explicit_date_window("最近一个月", now=NOW), now=NOW) == "month"
    assert provider_time_range_for(resolve_explicit_date_window("今年", now=NOW), now=NOW) == "year"
    assert provider_time_range_for(resolve_explicit_date_window("2026年11月12日", now=NOW), now=NOW) is None


def test_window_for_time_range() -> None:
    week = window_for_time_range("week", now=NOW)
    assert week is not None
    assert week.start == NOW - timedelta(days=6) and week.end == NOW
    assert window_for_time_range(None, now=NOW) is None


def test_window_helpers() -> None:
    window = resolve_explicit_date_window("最近一周", now=NOW)
    assert window is not None
    assert is_in_window(date(2026, 11, 10), window) is True
    assert is_in_window(date(2026, 11, 1), window) is False
    assert is_in_window(None, window) is True
    assert days_outside_window(date(2026, 11, 1), window) == 8
