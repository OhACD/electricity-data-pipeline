"""Fetch Statnett responses and archive them before any transformation."""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

from oslo_energy.ingestion.archive import Archive
from oslo_energy.ingestion.statnett_client import StatnettClient


@dataclass(frozen=True)
class RawIngestionResult:
    archive_path: Path
    from_date: date
    fetched_at: datetime


class StatnettIngestion:
    def __init__(self, client: StatnettClient, archive_dir: str | Path):
        self.client = client
        self.archive_dir = Path(archive_dir)

    def run(self, from_date: date) -> RawIngestionResult:
        raw_data = self.client.get_production_consumption(from_date.isoformat())
        fetched_at = datetime.now(timezone.utc)
        filename = (
            f"statnett_from_{from_date.isoformat()}_"
            f"{fetched_at:%Y%m%dT%H%M%S%fZ}_{uuid4().hex}.json"
        )
        archive_path = self.archive_dir / filename
        Archive(archive_path).write(raw_data)

        return RawIngestionResult(archive_path, from_date, fetched_at)