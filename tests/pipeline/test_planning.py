"""Check incremental range selection without network or database access."""

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

import pytest
import httpx

from oslo_energy.database.statnett_repository import PersistenceError, StatnettRepository
from oslo_energy.pipeline.planning import DateRange, plan_daily, plan_reconciliation
from oslo_energy.database import statnett_repository
from oslo_energy.ingestion.statnett_client import StatnettClient
from oslo_energy.pipeline import run_ingestion
from oslo_energy.database.statnett_repository import PersistenceResult
from oslo_energy.ingestion.archive import Archive


def test_daily_catches_up_and_chunks_empty_database():
    ranges = plan_daily(
        today=date(2026, 10, 8), start_date=date(2026, 8, 1), latest_period_end=None,
    )
    assert ranges == [
        DateRange(date(2026, 8, 1), date(2026, 8, 31)),
        DateRange(date(2026, 9, 1), date(2026, 10, 1)),
        DateRange(date(2026, 10, 2), date(2026, 10, 7)),
    ]


@pytest.mark.parametrize("latest, expected_start", [
    (datetime(2026, 10, 7, 22, tzinfo=timezone.utc), date(2026, 10, 5)),
    (datetime(2026, 9, 30, 22, tzinfo=timezone.utc), date(2026, 10, 1)),
    (datetime(2026, 10, 1, 10, tzinfo=timezone.utc), date(2026, 10, 1)),
])
def test_daily_overlap_and_outage_catchup(latest, expected_start):
    assert plan_daily(
        today=date(2026, 10, 8), start_date=date(2005, 1, 1), latest_period_end=latest,
    ) == [DateRange(expected_start, date(2026, 10, 7))]


def test_daily_respects_configured_start_and_excludes_today():
    assert plan_daily(
        today=date(2026, 10, 8), start_date=date(2026, 10, 7),
        latest_period_end=datetime(2026, 10, 7, 22, tzinfo=timezone.utc),
    ) == [DateRange(date(2026, 10, 7), date(2026, 10, 7))]
    assert plan_daily(
        today=date(2026, 10, 8), start_date=date(2026, 10, 8), latest_period_end=None,
    ) == []


def test_reconciliation_merges_repairs_and_refresh_without_duplicates():
    assert plan_reconciliation(
        today=date(2026, 10, 8), start_date=date(2026, 1, 1), refresh_days=3,
        repair_dates=[date(2025, 12, 31), date(2026, 2, 1), date(2026, 2, 1),
                      date(2026, 2, 2), date(2026, 10, 4), date(2026, 10, 8)],
        chunk_days=3,
    ) == [
        DateRange(date(2026, 2, 1), date(2026, 2, 2)),
        DateRange(date(2026, 10, 4), date(2026, 10, 6)),
        DateRange(date(2026, 10, 7), date(2026, 10, 7)),
    ]


def test_reconciliation_can_only_repair_gaps():
    assert plan_reconciliation(
        today=date(2026, 10, 8), start_date=date(2005, 1, 1),
        repair_dates=[], refresh_days=0,
    ) == []


@pytest.mark.parametrize("kwargs", [{"overlap_days": 0}, {"chunk_days": 0}])
def test_daily_rejects_invalid_policy(kwargs):
    with pytest.raises(ValueError):
        plan_daily(today=date(2026, 10, 8), start_date=date(2005, 1, 1),
                   latest_period_end=None, **kwargs)


def test_coverage_queries_use_utc_grid_and_both_null_metrics():
    queries = []

    class Cursor:
        def execute(self, query, params):
            queries.append((query, params))

        def fetchall(self):
            if "MAX" in queries[-1][0]:
                return [(None,)]
            return [(date(2025, 10, 26),)]

    class Connection:
        @contextmanager
        def cursor(self):
            yield Cursor()

    @contextmanager
    def factory():
        yield Connection()

    repository = StatnettRepository(factory)
    assert repository.latest_period_end() is None
    assert repository.dates_needing_repair(date(2025, 10, 26), date(2025, 10, 26)) == [date(2025, 10, 26)]
    query, (start, end) = queries[-1]
    assert start.utcoffset() == end.utcoffset() == timedelta(0)
    assert end - start == timedelta(hours=25)
    assert "current.period_start_utc IS NULL" in query
    assert "current.production IS NULL" in query and "current.consumption IS NULL" in query


def test_coverage_query_errors_are_reported_as_persistence_errors():
    def factory():
        raise RuntimeError("unavailable")

    with pytest.raises(PersistenceError, match="coverage query failed"):
        StatnettRepository(factory).latest_period_end()


@pytest.fixture
def fixed_job_clock(monkeypatch):
    class FixedClock(datetime):
        @classmethod
        def now(cls, zone):
            return datetime(2026, 10, 8, 3, tzinfo=zone)

    monkeypatch.setattr(run_ingestion, "datetime", FixedClock)


@pytest.mark.parametrize("mode", ["--daily", "--reconcile"])
def test_job_dry_run_only_reads_coverage(mode, fixed_job_clock, tmp_path, monkeypatch, capsys):
    class Repository:
        def latest_period_end(self):
            return datetime(2026, 10, 7, 22, tzinfo=timezone.utc)

        def dates_needing_repair(self, start, end):
            assert end == date(2026, 10, 7)
            return [date(2026, 2, 1)]

    monkeypatch.setattr(statnett_repository, "StatnettRepository", Repository)
    monkeypatch.setattr(run_ingestion, "StatnettClient", lambda: pytest.fail("dry run fetched"))
    assert run_ingestion.main([
        mode, "--dry-run", "--refresh-days", "3", "--archive-dir", str(tmp_path / "raw"),
    ]) == 0
    output = capsys.readouterr().out
    assert "2026-10-05 through 2026-10-07" in output
    assert not (tmp_path / "raw").exists()


@pytest.mark.parametrize("mode, expected_dates", [
    ("--daily", [date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7)]),
    ("--reconcile", [date(2026, 10, 1), date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7)]),
])
def test_jobs_archive_normalize_and_persist_each_chunk(mode, expected_dates, fixed_job_clock, tmp_path, monkeypatch):
    requests = []
    writes = []

    class Repository:
        def latest_period_end(self):
            return datetime(2026, 10, 7, 22, tzinfo=timezone.utc)

        def dates_needing_repair(self, start, end):
            return [date(2026, 10, 1), date(2026, 10, 5)]

        def persist(self, data, **kwargs):
            writes.append((data, kwargs))
            return PersistenceResult(len(writes), len(data.historical), len(data.historical), False)

    def respond(request):
        params = request.url.params
        start = int(params["FromInTicks"])
        end = int(params["ToInTicks"]) // 3600000 * 3600000
        count = (end - start) // 3600000 + 1
        requests.append((start, end))
        return httpx.Response(200, json={
            "StartPointUTC": start, "EndPointUTC": end, "PeriodTickMs": 3600000,
            "Production": [1] * count, "Consumption": [2] * count,
        })

    monkeypatch.setattr(statnett_repository, "StatnettRepository", Repository)
    monkeypatch.setattr(run_ingestion, "StatnettClient", lambda: StatnettClient(transport=httpx.MockTransport(respond)))
    assert run_ingestion.main([
        mode, "--refresh-days", "3", "--chunk-days", "1", "--archive-dir", str(tmp_path / "raw"),
        "--output-dir", str(tmp_path / "normalized"),
    ]) == 0
    assert len(requests) == len(writes) == len(expected_dates)
    assert [kwargs["requested_from_date"] for data, kwargs in writes] == expected_dates
    assert all(len(data.historical) == 24 for data, kwargs in writes)
    assert len(list((tmp_path / "raw").glob("*.json"))) == len(expected_dates)
    assert len(list((tmp_path / "normalized").glob("*/quality.json"))) == len(expected_dates)


def test_reconciliation_no_work_does_not_fetch(fixed_job_clock, tmp_path, monkeypatch):
    class Repository:
        def dates_needing_repair(self, start, end):
            return []

    monkeypatch.setattr(statnett_repository, "StatnettRepository", Repository)
    monkeypatch.setattr(run_ingestion, "StatnettClient", lambda: pytest.fail("empty plan fetched"))
    assert run_ingestion.main([
        "--reconcile", "--refresh-days", "0", "--output-dir", str(tmp_path / "outputs"),
    ]) == 0
    report = Archive(next((tmp_path / "outputs").glob("reconciliation_*.json"))).read()
    assert report["planned_request_count"] == 0
    assert report["requests"] == []


def test_reconciliation_continues_after_empty_response(fixed_job_clock, tmp_path, monkeypatch, capsys):
    requests = []
    writes = []

    class Repository:
        def dates_needing_repair(self, start, end):
            return [date(2008, 12, 30), date(2010, 12, 31)]

        def persist(self, data, **kwargs):
            writes.append(kwargs)
            return PersistenceResult(7, len(data.historical), len(data.historical), False)

    def respond(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, json={
                "StartPointUTC": 0.0, "EndPointUTC": 0.0, "PeriodTickMs": 0,
                "Production": [], "Consumption": [],
            })
        start = int(request.url.params["FromInTicks"])
        return httpx.Response(200, json={
            "StartPointUTC": start, "EndPointUTC": start + 23 * 3600000,
            "PeriodTickMs": 3600000, "Production": [1] * 24, "Consumption": [2] * 24,
        })

    monkeypatch.setattr(statnett_repository, "StatnettRepository", Repository)
    monkeypatch.setattr(run_ingestion, "StatnettClient", lambda: StatnettClient(transport=httpx.MockTransport(respond)))
    assert run_ingestion.main([
        "--reconcile", "--refresh-days", "0", "--archive-dir", str(tmp_path / "raw"),
        "--output-dir", str(tmp_path / "outputs"),
    ]) == 1
    assert len(requests) == 2 and len(writes) == 1
    assert len(list((tmp_path / "raw").glob("*.json"))) == 2
    assert len(list((tmp_path / "outputs").glob("*/quality.json"))) == 1
    report = Archive(next((tmp_path / "outputs").glob("reconciliation_*.json"))).read()
    assert report["planned_request_count"] == 2
    assert report["unresolved_request_count"] == 1
    assert [entry["status"] for entry in report["requests"]] == ["unresolved", "complete"]
    assert "continuing reconciliation" in capsys.readouterr().err


@pytest.mark.parametrize("partial_kind", ["absent", "null"])
def test_reconciliation_reports_unresolved_coverage_and_keeps_valid_rows(
    partial_kind, fixed_job_clock, tmp_path, monkeypatch, capsys,
):
    writes = []

    class Repository:
        def dates_needing_repair(self, start, end):
            return [date(2008, 6, 5)]

        def persist(self, data, **kwargs):
            writes.append(data)
            return PersistenceResult(7, len(data.historical), len(data.historical), False)

    def respond(request):
        start = int(request.url.params["FromInTicks"])
        count = 21 if partial_kind == "absent" else 24
        production = [1] * count
        if partial_kind == "null":
            production[0] = None
        return httpx.Response(200, json={
            "StartPointUTC": start, "EndPointUTC": start + (count - 1) * 3600000,
            "PeriodTickMs": 3600000, "Production": production, "Consumption": [2] * count,
        })

    monkeypatch.setattr(statnett_repository, "StatnettRepository", Repository)
    monkeypatch.setattr(run_ingestion, "StatnettClient", lambda: StatnettClient(transport=httpx.MockTransport(respond)))
    assert run_ingestion.main([
        "--reconcile", "--refresh-days", "0", "--archive-dir", str(tmp_path / "raw"),
        "--output-dir", str(tmp_path / "outputs"),
    ]) == 1
    report = Archive(next((tmp_path / "outputs").glob("reconciliation_*.json"))).read()
    assert report["unresolved_request_count"] == 1
    entry = report["requests"][0]
    assert entry["database_run_id"] == 7
    assert entry["coverage"]["expected_completed_hour_count"] == 24
    assert entry["coverage"]["missing_hour_count"] == (3 if partial_kind == "absent" else 0)
    assert entry["coverage"]["production_missing_count"] == (1 if partial_kind == "null" else 0)
    assert len(writes) == 1
    assert len(writes[0].historical) == (21 if partial_kind == "absent" else 24)
    assert "Requested coverage: expected 24 completed hours" in capsys.readouterr().out


def test_reconciliation_malformed_response_stops_and_reports_unattempted_ranges(
    fixed_job_clock, tmp_path, monkeypatch,
):
    requests = []

    class Repository:
        def dates_needing_repair(self, start, end):
            return [date(2008, 12, 30), date(2010, 12, 31)]

        def persist(self, data, **kwargs):
            pytest.fail("Malformed responses must not be persisted")

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={
            "StartPointUTC": 0, "EndPointUTC": 0, "PeriodTickMs": False,
            "Production": [], "Consumption": [],
        })

    monkeypatch.setattr(statnett_repository, "StatnettRepository", Repository)
    monkeypatch.setattr(run_ingestion, "StatnettClient", lambda: StatnettClient(transport=httpx.MockTransport(respond)))
    assert run_ingestion.main([
        "--reconcile", "--refresh-days", "0", "--archive-dir", str(tmp_path / "raw"),
        "--output-dir", str(tmp_path / "outputs"),
    ]) == 1
    assert len(requests) == 1
    report = Archive(next((tmp_path / "outputs").glob("reconciliation_*.json"))).read()
    assert report["failed_request_count"] == 1
    assert report["unattempted_request_count"] == 1
    assert report["requests"][0]["status"] == "failed"


@pytest.mark.parametrize("mode", ["--daily", "--reconcile"])
def test_job_database_failure_stops_chunks_and_retains_artifacts(mode, fixed_job_clock, tmp_path, monkeypatch, capsys):
    requests = []

    class Repository:
        def latest_period_end(self):
            return datetime(2026, 10, 7, 22, tzinfo=timezone.utc)

        def dates_needing_repair(self, start, end):
            return []

        def persist(self, data, **kwargs):
            raise PersistenceError("simulated failure")

    def respond(request):
        start = int(request.url.params["FromInTicks"])
        requests.append(request)
        return httpx.Response(200, json={
            "StartPointUTC": start, "EndPointUTC": start + 23 * 3600000,
            "PeriodTickMs": 3600000, "Production": [1] * 24, "Consumption": [2] * 24,
        })

    monkeypatch.setattr(statnett_repository, "StatnettRepository", Repository)
    monkeypatch.setattr(run_ingestion, "StatnettClient", lambda: StatnettClient(transport=httpx.MockTransport(respond)))
    assert run_ingestion.main([
        mode, "--refresh-days", "3", "--chunk-days", "1", "--archive-dir", str(tmp_path / "raw"),
        "--output-dir", str(tmp_path / "normalized"),
    ]) == 1
    assert len(requests) == 1
    assert len(list((tmp_path / "raw").glob("*.json"))) == 1
    assert len(list((tmp_path / "normalized").glob("*/quality.json"))) == 1
    assert "simulated failure" in capsys.readouterr().err
    if mode == "--reconcile":
        report = Archive(next((tmp_path / "normalized").glob("reconciliation_*.json"))).read()
        assert report["failed_request_count"] == 1
        assert report["unattempted_request_count"] == 2


@pytest.mark.parametrize("args", [
    ["--daily", "--from-date", "2025-01-01"],
    ["--daily", "--to-date", "2025-01-01"],
    ["--daily", "--replay", "raw.json"],
    ["--dry-run"], ["--start-date", "2025-01-01"],
    ["--daily", "--overlap-days", "0"], ["--reconcile", "--refresh-days", "-1"],
])
def test_job_invalid_arguments_fail_before_fetch(args, monkeypatch):
    monkeypatch.setattr(run_ingestion, "StatnettClient", lambda: pytest.fail("invalid arguments fetched"))
    with pytest.raises(SystemExit) as exc:
        run_ingestion.main(args)
    assert exc.value.code == 2
