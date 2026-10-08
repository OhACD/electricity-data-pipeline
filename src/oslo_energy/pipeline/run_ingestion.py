"""Command-line entry point for hourly ingestion and interval-aware archive replay."""

import argparse
import sys
from collections.abc import Sequence
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from oslo_energy.ingestion.archive import Archive
from oslo_energy.ingestion.statnett_client import StatnettClient, StatnettClientError
from oslo_energy.pipeline.ingestion import StatnettIngestion
from oslo_energy.pipeline.normalization import EmptyStatnettResponse, normalize_archive
from oslo_energy.pipeline.planning import DateRange, plan_daily, plan_reconciliation


def positive_int(value: str) -> int:
    """Parse a strictly positive job policy value."""
    try:
        parsed = int(value)
        if parsed < 1:
            raise ValueError(value)
        return parsed
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a positive integer") from exc


def nonnegative_int(value: str) -> int:
    """Parse a nonnegative refresh window; zero disables refreshing."""
    try:
        parsed = int(value)
        if parsed < 0:
            raise ValueError(value)
        return parsed
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a nonnegative integer") from exc


def parse_date(value: str) -> date:
    """Parse an exact YYYY-MM-DD date or raise ArgumentTypeError."""
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError(value)
        return parsed
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a date in YYYY-MM-DD format") from exc


def parse_fetched_at(value: str) -> datetime:
    """Parse an aware ISO timestamp, accepting Z, or raise ArgumentTypeError."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(value)
        return parsed
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "expected an ISO timestamp with timezone, such as 2026-10-06T00:56:03Z"
        ) from exc


def save_reconciliation_report(output_dir: Path, planned_count: int, requests: list[dict]) -> None:
    """Preserve every attempted range and distinguish incomplete coverage from failed requests."""
    unresolved_count = sum(report["status"] == "unresolved" for report in requests)
    failed_count = sum(report["status"] == "failed" for report in requests)
    complete_count = sum(report["status"] == "complete" for report in requests)
    unattempted_count = planned_count - len(requests)
    report_path = output_dir / f"reconciliation_{uuid4().hex}.json"
    Archive(report_path).write({
        "planned_request_count": planned_count,
        "complete_request_count": complete_count,
        "unresolved_request_count": unresolved_count,
        "failed_request_count": failed_count,
        "unattempted_request_count": unattempted_count,
        "requests": requests,
    })
    print(
        f"Reconciliation summary: {complete_count} complete requests; "
        f"{unresolved_count} unresolved; {failed_count} failed; {unattempted_count} unattempted. "
        f"Saved report to {report_path}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run raw ingestion or offline replay, with optional database persistence.

    Live normalization follows raw archival; replay requires the original
    aware fetch timestamp and makes no API request. Persistence requires live
    normalization or replay and does not certify training readiness.

    Return 0 on success or 1 after reporting a caught client, filesystem or
    validation error to stderr. Argument errors raise SystemExit via argparse.
    ``argv=None`` reads arguments from the process command line.
    """
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
    source.add_argument("--daily", action="store_true", help="catch up and refresh recent days; normalize and persist")
    source.add_argument("--reconcile", action="store_true", help="repair gaps/nulls and refresh recent history; normalize and persist")
    parser.add_argument("--start-date", type=parse_date, help="earliest job date (default: 2005-01-01)")
    parser.add_argument("--overlap-days", type=positive_int, default=3, help="daily overlap in completed Oslo days (default: 3)")
    parser.add_argument("--refresh-days", type=nonnegative_int, default=90, help="reconciliation refresh days; 0 repairs only (default: 90)")
    parser.add_argument("--chunk-days", type=positive_int, default=31, help="maximum local days per job request (default: 31)")
    parser.add_argument("--dry-run", action="store_true", help="inspect job coverage and print ranges without fetching or writing")
    parser.add_argument("--to-date", type=parse_date, help="inclusive Oslo end date for live ingestion")
    parser.add_argument("--normalize", action="store_true", help="normalize the response after raw archival")
    parser.add_argument("--persist", action="store_true", help="persist completed hourly candidates to PostgreSQL")
    parser.add_argument("--fetched-at", type=parse_fetched_at, help="original fetch time; required for replay")
    parser.add_argument(
        "--output-dir", type=Path, default=Path("data/normalized/statnett"),
        help="normalized output directory (default: data/normalized/statnett)",
    )
    args = parser.parse_args(argv)
    job_mode = args.daily or args.reconcile
    if (args.dry_run or args.start_date is not None) and not job_mode:
        parser.error("--dry-run and --start-date require --daily or --reconcile")
    if job_mode and args.to_date is not None:
        parser.error("job modes end yesterday; --to-date is only valid for manual ingestion")
    if args.to_date is not None and args.replay is not None:
        parser.error("--to-date is only valid for live ingestion")
    if args.to_date is not None and args.to_date < args.from_date:
        parser.error("--to-date must not precede --from-date")
    if args.replay is not None and args.fetched_at is None:
        parser.error("--replay requires --fetched-at to retain the original date cutoff")
    if args.replay is None and args.fetched_at is not None:
        parser.error("--fetched-at is only valid with --replay")
    if args.persist and args.replay is None and not args.normalize and not job_mode:
        parser.error("--persist requires --normalize for live ingestion")

    request_reports = []
    ranges = []
    try:
        repository = None
        if args.persist or job_mode:
            from oslo_energy.database.statnett_repository import StatnettRepository

            repository = StatnettRepository()
        if job_mode:
            today = datetime.now(ZoneInfo("Europe/Oslo")).date()
            start_date = args.start_date or date(2005, 1, 1)
            if start_date >= today:
                raise ValueError("--start-date must precede today in Europe/Oslo")
            if args.daily:
                ranges = plan_daily(
                    today=today, start_date=start_date,
                    latest_period_end=repository.latest_period_end(),
                    overlap_days=args.overlap_days, chunk_days=args.chunk_days,
                )
            else:
                ranges = plan_reconciliation(
                    today=today, start_date=start_date,
                    repair_dates=repository.dates_needing_repair(start_date, today - timedelta(days=1)),
                    refresh_days=args.refresh_days, chunk_days=args.chunk_days,
                )
            print(f"Planned {len(ranges)} bounded requests through yesterday in Europe/Oslo")
            for request_range in ranges:
                print(
                    f"Request {request_range.from_date.isoformat()} through {request_range.to_date.isoformat()}"
                )
            if args.dry_run:
                return 0
        else:
            ranges = [DateRange(args.from_date, args.to_date)]

        for request_range in ranges:
            request_report = {
                "from_date": request_range.from_date.isoformat(),
                "to_date": request_range.to_date.isoformat() if request_range.to_date else None,
                "status": "failed",
            }
            request_reports.append(request_report)
            if args.replay is None:
                result = StatnettIngestion(StatnettClient(), args.archive_dir).run(
                    request_range.from_date, to_date=request_range.to_date
                )
                archive_path = result.archive_path
                fetched_at = result.fetched_at
                print(f"Requested hourly Statnett API data from {result.from_date.isoformat()}")
                print(f"Archived raw response to {archive_path}")
            else:
                archive_path = args.replay
                fetched_at = args.fetched_at

            if args.normalize or args.replay is not None or job_mode:
                request_report.update(
                    source_archive=str(archive_path.resolve()), fetched_at=fetched_at.isoformat(),
                )
                try:
                    normalized = normalize_archive(
                        archive_path, fetched_at=fetched_at, output_dir=args.output_dir,
                        requested_range=request_range if args.replay is None and request_range.to_date else None,
                    )
                except EmptyStatnettResponse as exc:
                    if not args.reconcile:
                        raise
                    request_report.update(status="unresolved", reason=str(exc))
                    print(
                        f"Unresolved {request_report['from_date']} through {request_report['to_date']}: "
                        f"{exc}; raw archive retained, continuing reconciliation",
                        file=sys.stderr,
                    )
                    continue
                request_report["status"] = "complete"
                quality = normalized.data.quality
                if normalized.coverage is not None:
                    coverage = normalized.coverage
                    request_report["coverage"] = asdict(coverage)
                    print(
                        f"Requested coverage: expected {coverage.expected_completed_hour_count} completed hours; "
                        f"returned {coverage.returned_completed_hour_count}; absent {coverage.missing_hour_count}; "
                        f"null production {coverage.production_missing_count}; "
                        f"null consumption {coverage.consumption_missing_count}"
                    )
                    if coverage.unresolved:
                        request_report.update(status="unresolved", reason="Requested coverage has absent or null hours")
                        print(f"Unresolved coverage: {request_report['reason']}", file=sys.stderr)
                print(
                    f"Mapped {quality.raw_slot_count} raw slots to {quality.observation_count} "
                    f"{quality.frequency} observations across {quality.expected_date_count} Norwegian dates"
                )
                print(
                    f"Padding: {quality.padding_count}; missing production: {quality.production_missing_count}; "
                    f"missing consumption: {quality.consumption_missing_count}"
                )
                print(
                    f"Completed-period candidates: {quality.historical_count}; "
                    f"incomplete: {quality.incomplete_count}; future quarantined: {quality.forecast_count}"
                )
                print(f"Saved normalized artifacts to {normalized.output_dir}")
                print("Training readiness blocked: provider units, API/export differences and source finality require validation")
                if repository is not None:
                    result = repository.persist(
                        normalized.data,
                        archive_path=archive_path,
                        fetched_at=fetched_at,
                        requested_from_date=request_range.from_date if args.replay is None else None,
                    )
                    action = "Already persisted" if result.idempotent else "Persisted"
                    print(
                        f"{action} {result.candidate_count} hourly candidates in database run {result.run_id}; "
                        f"current rows inserted/updated: {result.inserted_or_updated_count}"
                    )
                    request_report["database_run_id"] = result.run_id
        if args.reconcile:
            save_reconciliation_report(args.output_dir, len(ranges), request_reports)
        if job_mode and any(report["status"] == "unresolved" for report in request_reports):
            return 1
    except (StatnettClientError, OSError, ValueError) as exc:
        print(f"Ingestion failed: {exc}", file=sys.stderr)
        if args.reconcile and request_reports:
            request_reports[-1].update(status="failed", reason=str(exc))
            try:
                save_reconciliation_report(args.output_dir, len(ranges), request_reports)
            except (OSError, ValueError) as report_error:
                print(f"Could not save reconciliation report: {report_error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())