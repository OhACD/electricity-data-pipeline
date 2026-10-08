"""Fetch Statnett responses and archive them before any transformation."""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

from oslo_energy.ingestion.archive import Archive
from oslo_energy.ingestion.statnett_client import StatnettClient


@dataclass(frozen=True)
class RawIngestionResult:
    """Raw archive path, requested start date and post-request UTC fetch time."""

    archive_path: Path
    from_date: date
    fetched_at: datetime
    to_date: date | None = None


class StatnettIngestion:
    """Archive raw Statnett responses before normalization or persistence."""

    def __init__(self, client: StatnettClient, archive_dir: str | Path):
        """Use the supplied API client and raw archive directory."""
        self.client = client
        self.archive_dir = Path(archive_dir)

    def run(self, from_date: date, *, to_date: date | None = None) -> RawIngestionResult:
        """Fetch hourly data and write a uniquely named raw JSON archive.

        Record the UTC fetch time after the request completes and return it
        with the archive path and requested date. Request and archive errors
        propagate; a failed request does not create an archive.
        """
        raw_data = self.client.get_production_consumption(
            from_date.isoformat(), to_date=to_date.isoformat() if to_date else None
        )
        fetched_at = datetime.now(timezone.utc)
        filename = (
            f"statnett_from_{from_date.isoformat()}_"
            f"{fetched_at:%Y%m%dT%H%M%S%fZ}_{uuid4().hex}.json"
        )
        archive_path = self.archive_dir / filename
        Archive(archive_path).write(raw_data)

        return RawIngestionResult(archive_path, from_date, fetched_at, to_date)