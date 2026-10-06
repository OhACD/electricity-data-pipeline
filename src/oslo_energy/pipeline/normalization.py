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
    output_dir: Path
    data: NormalizedStatnettData


def normalize_archive(
    archive_path: Path, *, fetched_at: datetime, output_dir: Path
) -> NormalizationResult:
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