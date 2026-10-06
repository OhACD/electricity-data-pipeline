"""Command-line entry point for raw ingestion and calendar-aware archive replay."""

import argparse
import sys
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

from oslo_energy.ingestion.statnett_client import StatnettClient, StatnettClientError
from oslo_energy.pipeline.ingestion import StatnettIngestion
from oslo_energy.pipeline.normalization import normalize_archive


def parse_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError(value)
        return parsed
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a date in YYYY-MM-DD format") from exc


def parse_fetched_at(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(value)
        return parsed
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "expected an ISO timestamp with timezone, such as 2026-10-06T00:56:03Z"
        ) from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch Statnett data or normalize an archive.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--from-date",
        type=parse_date,
        default=date(2005, 1, 1),
        help="requested start date in YYYY-MM-DD format (default: 2005-01-01)",
    )
    parser.add_argument(
        "--archive-dir",
        type=Path,
        default=Path("data/raw/statnett"),
        help="archive directory (default: data/raw/statnett, relative to working directory)",
    )
    source.add_argument("--replay", type=Path, help="normalize an existing archive without an API request")
    parser.add_argument("--normalize", action="store_true", help="normalize the response after raw archival")
    parser.add_argument("--fetched-at", type=parse_fetched_at, help="original fetch time; required for replay")
    parser.add_argument(
        "--output-dir", type=Path, default=Path("data/normalized/statnett"),
        help="normalized output directory (default: data/normalized/statnett)",
    )
    args = parser.parse_args(argv)
    if args.replay is not None and args.fetched_at is None:
        parser.error("--replay requires --fetched-at to retain the original date cutoff")
    if args.replay is None and args.fetched_at is not None:
        parser.error("--fetched-at is only valid with --replay")

    try:
        if args.replay is None:
            result = StatnettIngestion(StatnettClient(), args.archive_dir).run(args.from_date)
            archive_path = result.archive_path
            fetched_at = result.fetched_at
            print(f"Requested Statnett data from {result.from_date.isoformat()}")
            print(f"Archived raw response to {archive_path}")
        else:
            archive_path = args.replay
            fetched_at = args.fetched_at

        if args.normalize or args.replay is not None:
            normalized = normalize_archive(
                archive_path, fetched_at=fetched_at, output_dir=args.output_dir
            )
            quality = normalized.data.quality
            print(f"Mapped {quality.raw_slot_count} raw slots to {quality.observation_count} Norwegian dates")
            print(
                f"Padding: {quality.padding_count}; missing production: {quality.production_missing_count}; "
                f"missing consumption: {quality.consumption_missing_count}"
            )
            print(
                f"Completed-day candidates: {quality.historical_count}; "
                f"incomplete: {quality.incomplete_count}; future quarantined: {quality.forecast_count}"
            )
            print(f"Saved normalized artifacts to {normalized.output_dir}")
            print("Training readiness blocked: daily provider units/aggregation require reference validation")
    except (StatnettClientError, OSError, ValueError) as exc:
        print(f"Ingestion failed: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())