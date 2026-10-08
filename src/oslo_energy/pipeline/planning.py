"""Plan bounded Oslo-date requests from committed hourly coverage."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class DateRange:
    """Inclusive local dates for one API request."""

    from_date: date
    to_date: date | None


def _chunks(start: date, end: date, chunk_days: int) -> list[DateRange]:
    if chunk_days < 1:
        raise ValueError("chunk_days must be positive")
    ranges = []
    while start <= end:
        chunk_end = min(end, start + timedelta(days=chunk_days - 1))
        ranges.append(DateRange(start, chunk_end))
        start = chunk_end + timedelta(days=1)
    return ranges


def plan_daily(
    *, today: date, start_date: date, latest_period_end: datetime | None,
    overlap_days: int = 3, chunk_days: int = 31,
) -> list[DateRange]:
    """Catch up from the last committed hour and overlap recent completed local days."""
    if overlap_days < 1:
        raise ValueError("overlap_days must be positive")
    end = today - timedelta(days=1)
    start = start_date
    if latest_period_end is not None:
        if latest_period_end.tzinfo is None or latest_period_end.utcoffset() is None:
            raise ValueError("latest_period_end must be timezone-aware")
        next_date = latest_period_end.astimezone(ZoneInfo("Europe/Oslo")).date()
        overlap_start = end - timedelta(days=overlap_days - 1)
        start = max(start_date, min(next_date, overlap_start))
    return _chunks(start, end, chunk_days)


def plan_reconciliation(
    *, today: date, start_date: date, repair_dates: list[date],
    refresh_days: int = 90, chunk_days: int = 31,
) -> list[DateRange]:
    """Merge repair dates with a recent refresh and split contiguous dates into chunks."""
    if refresh_days < 0:
        raise ValueError("refresh_days must not be negative")
    if chunk_days < 1:
        raise ValueError("chunk_days must be positive")
    end = today - timedelta(days=1)
    dates = {value for value in repair_dates if start_date <= value <= end}
    refresh_start = max(start_date, end - timedelta(days=refresh_days - 1))
    while refresh_start <= end:
        dates.add(refresh_start)
        refresh_start += timedelta(days=1)
    ranges = []
    ordered = sorted(dates)
    if not ordered:
        return ranges
    start = previous = ordered[0]
    for current in ordered[1:]:
        if current != previous + timedelta(days=1):
            ranges.extend(_chunks(start, previous, chunk_days))
            start = current
        previous = current
    ranges.extend(_chunks(start, previous, chunk_days))
    return ranges
