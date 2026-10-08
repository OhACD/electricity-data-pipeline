"""Replay a raw archive into inspectable observation candidates and a quality report."""

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from oslo_energy.ingestion.archive import Archive
from oslo_energy.transformation.statnett_normalizer import (
    NormalizedStatnettData,
    StatnettNormalizer,
)


@dataclass(frozen=True)
class NormalizationResult:
    """Normalized candidate data and the directory containing its artifacts."""

    output_dir: Path
    data: NormalizedStatnettData


def normalize_archive(
    archive_path: Path, *, fetched_at: datetime, output_dir: Path
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
    data = StatnettNormalizer().normalize(source, fetched_at=fetched_at)
    destination = output_dir / f"{archive_path.stem}_{uuid4().hex}"
    destination.mkdir(parents=True, exist_ok=False)
    data.historical.to_csv(destination / "historical_candidates.csv", index=False)
    data.incomplete.to_csv(destination / "incomplete.csv", index=False)
    data.forecast.to_csv(destination / "future_quarantine.csv", index=False)
    Archive(destination / "quality.json").write({
        "source_archive": str(archive_path.resolve()),
        **asdict(data.quality),
    })
    return NormalizationResult(destination, data)