"""Shared helper utilities."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional


def parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def format_iso(dt: datetime) -> str:
    return dt.isoformat()


def add_months(dt: datetime, months: int) -> datetime:
    year = dt.year + (dt.month - 1 + months) // 12
    month = (dt.month - 1 + months) % 12 + 1
    day = min(dt.day, _days_in_month(year, month))
    return dt.replace(year=year, month=month, day=day)


def add_years(dt: datetime, years: int) -> datetime:
    try:
        return dt.replace(year=dt.year + years)
    except ValueError:
        # Handle February 29th on non-leap years by clamping to Feb 28th
        if dt.month == 2 and dt.day == 29:
            return dt.replace(year=dt.year + years, month=2, day=28)
        raise


def _days_in_month(year: int, month: int) -> int:
    next_month = datetime(year + (month // 12), month % 12 + 1, 1)
    this_month = datetime(year, month, 1)
    return (next_month - this_month).days
