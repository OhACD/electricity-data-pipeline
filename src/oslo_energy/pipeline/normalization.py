"""Replay a raw archive into inspectable observation candidates and a quality report."""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd

from oslo_energy.ingestion.archive import Archive
from oslo_energy.pipeline.planning import DateRange
from oslo_energy.transformation.statnett_normalizer import (
    NormalizedStatnettData,
    StatnettNormalizer,
)


class EmptyStatnettResponse(ValueError):
    """The provider returned its exact zero-metadata, empty-array sentinel."""


@dataclass(frozen=True)
class RequestCoverage:
    """Compare completed returned hours against the original bounded request."""

    from_date: str
    to_date: str
    expected_completed_hour_count: int
    returned_completed_hour_count: int
    missing_hour_count: int
    production_missing_count: int
    consumption_missing_count: int

    @property
    def unresolved(self) -> bool:
        return bool(self.missing_hour_count or self.production_missing_count or self.consumption_missing_count)


def request_coverage(
    data: NormalizedStatnettData, requested_range: DateRange, fetched_at: datetime,
) -> RequestCoverage:
    """Count absent and null hours without manufacturing observations."""
    if requested_range.to_date is None or requested_range.to_date < requested_range.from_date:
        raise ValueError("Request coverage requires an ordered bounded date range")
    if data.quality.frequency != "hourly":
        raise ValueError("Request coverage requires hourly observations")
    local_timezone = ZoneInfo("Europe/Oslo")
    start = datetime.combine(requested_range.from_date, datetime.min.time(), local_timezone).astimezone(timezone.utc)
    end = datetime.combine(requested_range.to_date + timedelta(days=1), datetime.min.time(), local_timezone).astimezone(timezone.utc)
    if (
        (data.observations["period_start_utc"] < start).any()
        or (data.observations["period_end_utc"] > end).any()
    ):
        raise ValueError("Returned observations fall outside the requested date range")
    completed_end = min(end, fetched_at.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0))
    expected = pd.date_range(start=start, end=completed_end, freq="h", inclusive="left")
    returned = pd.DatetimeIndex(data.historical["period_start_utc"])
    frame = data.historical.loc[returned.isin(expected)]
    return RequestCoverage(
        requested_range.from_date.isoformat(), requested_range.to_date.isoformat(),
        len(expected), len(frame), len(expected.difference(returned)),
        int(frame["production"].isna().sum()), int(frame["consumption"].isna().sum()),
    )


@dataclass(frozen=True)
class NormalizationResult:
    """Normalized candidate data and the directory containing its artifacts."""

    output_dir: Path
    data: NormalizedStatnettData
    coverage: RequestCoverage | None = None


def normalize_archive(
    archive_path: Path, *, fetched_at: datetime, output_dir: Path,
    requested_range: DateRange | None = None,
) -> NormalizationResult:
    """Replay raw data into candidate CSVs and a provenance-bearing report.

    ``fetched_at`` must be the original timezone-aware fetch timestamp used
    for completed, incomplete and future period classification. Preserve the
    source archive and provider units, writing each replay to a new directory
    beneath ``output_dir``. The quality report retains ``training_ready=False``.

    Archive, validation and filesystem errors propagate. CSV writes precede
    ``quality.json``; a write failure can leave partial output artifacts.
    """
    source = Archive(archive_path).read()
    if (
        isinstance(source, dict)
        and all(type(source.get(field)) in (int, float) and source[field] == 0
            for field in ("StartPointUTC", "EndPointUTC", "PeriodTickMs"))
        and source.get("Production") == []
        and source.get("Consumption") == []
    ):
        raise EmptyStatnettResponse("Statnett returned no observations for this request")
    data = StatnettNormalizer().normalize(source, fetched_at=fetched_at)
    coverage = request_coverage(data, requested_range, fetched_at) if requested_range else None
    destination = output_dir / f"{archive_path.stem}_{uuid4().hex}"
    destination.mkdir(parents=True, exist_ok=False)
    data.historical.to_csv(destination / "historical_candidates.csv", index=False)
    data.incomplete.to_csv(destination / "incomplete.csv", index=False)
    data.forecast.to_csv(destination / "future_quarantine.csv", index=False)
    Archive(destination / "quality.json").write({
        "source_archive": str(archive_path.resolve()),
        **asdict(data.quality),
        **({"request_coverage": asdict(coverage)} if coverage else {}),
    })
    return NormalizationResult(destination, data, coverage)