"""Verify replay provenance, normalization partitions, and CLI validation failures."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pandas as pd
import pytest

from oslo_energy.ingestion.archive import Archive
from oslo_energy.ingestion.statnett_client import StatnettClient
from oslo_energy.pipeline import run_ingestion
from oslo_energy.pipeline.normalization import EmptyStatnettResponse, normalize_archive
from oslo_energy.pipeline.planning import DateRange


def save_source(tmp_path):
    """Archive three legacy daily slots with a missing first production value."""
    path = tmp_path / "raw.json"
    Archive(path).write({
        "StartPointUTC": pd.Timestamp("2025-01-01", tz="Europe/Oslo").timestamp() * 1000,
        "EndPointUTC": pd.Timestamp("2025-01-03", tz="Europe/Oslo").timestamp() * 1000,
        "PeriodTickMs": 86400000,
        "Production": [None, 2, 3],
        "Consumption": [4, 5, 6],
    })
    return path


def test_replay_preserves_raw_and_separates_outputs(tmp_path):
    """Replay preserves raw bytes and missing values while partitioning days by fetch time."""
    path = save_source(tmp_path)
    original = path.read_bytes()
    fetched_at = datetime(2025, 1, 2, 12, tzinfo=timezone.utc)

    result = normalize_archive(path, fetched_at=fetched_at, output_dir=tmp_path / "normalized")

    assert path.read_bytes() == original
    historical = pd.read_csv(result.output_dir / "historical_candidates.csv")
    incomplete = pd.read_csv(result.output_dir / "incomplete.csv")
    future = pd.read_csv(result.output_dir / "future_quarantine.csv")
    assert historical["observation_date"].tolist() == ["2025-01-01"]
    assert pd.isna(historical.iloc[0]["production"])
    assert incomplete["observation_date"].tolist() == ["2025-01-02"]
    assert future["observation_date"].tolist() == ["2025-01-03"]
    report = Archive(result.output_dir / "quality.json").read()
    assert report["source_archive"] == str(path.resolve())
    assert report["fetched_at_utc"] == "2025-01-02T12:00:00+00:00"
    assert report["historical_count"] == report["incomplete_count"] == report["forecast_count"] == 1
    assert report["training_ready"] is False


def test_repeated_replay_does_not_overwrite_previous_outputs(tmp_path):
    """Repeated replay uses distinct output directories and preserves the earlier report."""
    path = save_source(tmp_path)
    options = {"fetched_at": datetime(2025, 1, 4, tzinfo=timezone.utc), "output_dir": tmp_path / "outputs"}

    first = normalize_archive(path, **options)
    original = (first.output_dir / "quality.json").read_bytes()
    second = normalize_archive(path, **options)

    assert first.output_dir != second.output_dir
    assert (first.output_dir / "quality.json").read_bytes() == original


def test_cli_replay_does_not_create_api_client(tmp_path, monkeypatch, capsys):
    """Offline replay succeeds without an API client and reports blocked training readiness."""
    path = save_source(tmp_path)

    def unexpected_client():
        """Fail if offline replay constructs an API client."""
        pytest.fail("Replay must not create an API client")

    monkeypatch.setattr(run_ingestion, "StatnettClient", unexpected_client)

    assert run_ingestion.main([
        "--replay", str(path), "--fetched-at", "2025-01-04T00:00:00Z",
        "--output-dir", str(tmp_path / "outputs"),
    ]) == 0
    output = capsys.readouterr()
    assert "Mapped 3 raw slots to 3 daily observations" in output.out
    assert "Training readiness blocked" in output.out


@pytest.mark.parametrize(
    "args", [["--replay", "raw.json"], ["--fetched-at", "2025-01-04T00:00:00Z"],
             ["--replay", "raw.json", "--fetched-at", "2025-01-04"],
             ["--replay", "raw.json", "--from-date", "2025-01-01"]],
)
def test_invalid_replay_context_fails_before_fetch(args, monkeypatch):
    """Invalid replay arguments cause parser exit code 2 before client construction."""
    def unexpected_client():
        """Fail if invalid replay arguments reach client construction."""
        pytest.fail("Invalid replay context must not fetch")

    monkeypatch.setattr(run_ingestion, "StatnettClient", unexpected_client)

    with pytest.raises(SystemExit) as exc:
        run_ingestion.main(args)

    assert exc.value.code == 2


def test_invalid_archive_has_no_normalized_output(tmp_path, capsys):
    """An archive missing required fields fails replay without creating normalized output."""
    path = tmp_path / "invalid.json"
    Archive(path).write({"Production": []})
    output_dir = tmp_path / "outputs"

    assert run_ingestion.main([
        "--replay", str(path), "--fetched-at", "2025-01-04T00:00:00Z",
        "--output-dir", str(output_dir),
    ]) == 1
    assert not output_dir.exists()
    assert "required fields" in capsys.readouterr().err


def test_live_normalization_archives_before_validation_failure(tmp_path, monkeypatch):
    """Live ingestion archives an invalid payload before normalization rejects it."""
    monkeypatch.setattr(run_ingestion, "StatnettClient", lambda: StatnettClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"raw": True}))
    ))
    archive_dir = tmp_path / "raw"
    output_dir = tmp_path / "outputs"

    assert run_ingestion.main([
        "--normalize", "--archive-dir", str(archive_dir), "--output-dir", str(output_dir)
    ]) == 1
    paths = list(archive_dir.glob("*.json"))
    assert len(paths) == 1
    assert Archive(paths[0]).read() == {"raw": True}
    assert not output_dir.exists()


def test_cli_fetch_and_normalize_preserves_source_and_quarantines_future(tmp_path, monkeypatch):
    """Live hourly normalization preserves raw data and reports all three time partitions."""
    source = {
        "StartPointUTC": int(pd.Timestamp("2025-01-01T11:00:00Z").timestamp() * 1000),
        "EndPointUTC": int(pd.Timestamp("2025-01-01T13:00:00Z").timestamp() * 1000),
        "PeriodTickMs": 3600000,
        "Production": [None, 2, 3],
        "Consumption": [4, 5, 6],
    }
    fetched_at = datetime(2025, 1, 1, 12, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "oslo_energy.pipeline.ingestion.datetime",
        SimpleNamespace(now=lambda zone: fetched_at),
    )
    monkeypatch.setattr(run_ingestion, "StatnettClient", lambda: StatnettClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=source))
    ))
    archive_dir = tmp_path / "raw"
    output_dir = tmp_path / "outputs"

    assert run_ingestion.main([
        "--normalize", "--archive-dir", str(archive_dir), "--output-dir", str(output_dir)
    ]) == 0

    paths = list(archive_dir.glob("*.json"))
    assert len(paths) == 1
    assert Archive(paths[0]).read() == source
    reports = list(output_dir.glob("*/quality.json"))
    assert len(reports) == 1
    report = Archive(reports[0]).read()
    assert report["fetched_at_utc"] == fetched_at.isoformat()
    assert report["historical_count"] == report["incomplete_count"] == report["forecast_count"] == 1
    assert report["frequency"] == "hourly"
    assert report["period_tick_ms"] == 3600000
    historical = pd.read_csv(reports[0].parent / "historical_candidates.csv")
    assert historical["observation_date"].tolist() == ["2025-01-01"]


def test_replay_missing_archive_returns_nonzero(tmp_path, capsys):
    """A missing replay archive returns code 1 and reports ingestion failure."""
    assert run_ingestion.main([
        "--replay", str(tmp_path / "missing.json"), "--fetched-at", "2025-01-04T00:00:00Z"
    ]) == 1
    assert "Ingestion failed" in capsys.readouterr().err


@pytest.mark.parametrize("local_date, expected_hours", [
    (date(2025, 3, 30), 23), (date(2025, 10, 26), 25), (date(2025, 1, 1), 24),
])
def test_requested_coverage_counts_absent_and_null_hours(tmp_path, local_date, expected_hours):
    start = pd.Timestamp(local_date, tz="Europe/Oslo").tz_convert("UTC")
    path = tmp_path / "partial.json"
    count = expected_hours - 3
    Archive(path).write({
        "StartPointUTC": int((start + pd.Timedelta(hours=2)).timestamp() * 1000),
        "EndPointUTC": int((start + pd.Timedelta(hours=expected_hours - 2)).timestamp() * 1000),
        "PeriodTickMs": 3600000,
        "Production": [None] + [1] * (count - 1), "Consumption": [2] * count,
    })
    result = normalize_archive(
        path, fetched_at=start.to_pydatetime() + timedelta(days=2), output_dir=tmp_path / "outputs",
        requested_range=DateRange(local_date, local_date),
    )
    coverage = result.coverage
    assert coverage.expected_completed_hour_count == expected_hours
    assert coverage.returned_completed_hour_count == count
    assert coverage.missing_hour_count == 3
    assert coverage.production_missing_count == 1
    assert coverage.consumption_missing_count == 0
    assert coverage.unresolved
    report = Archive(result.output_dir / "quality.json").read()
    assert report["request_coverage"]["missing_hour_count"] == 3
    assert len(result.data.observations) == count


def test_coverage_does_not_count_unfinished_hours_as_absent(tmp_path):
    start = pd.Timestamp("2025-01-01", tz="Europe/Oslo").tz_convert("UTC")
    path = tmp_path / "ongoing.json"
    Archive(path).write({
        "StartPointUTC": int(start.timestamp() * 1000),
        "EndPointUTC": int((start + pd.Timedelta(hours=2)).timestamp() * 1000),
        "PeriodTickMs": 3600000, "Production": [1, 2, 3], "Consumption": [4, 5, 6],
    })
    result = normalize_archive(
        path, fetched_at=start.to_pydatetime() + timedelta(hours=2, minutes=30),
        output_dir=tmp_path / "outputs", requested_range=DateRange(date(2025, 1, 1), date(2025, 1, 1)),
    )
    assert result.coverage.expected_completed_hour_count == 2
    assert result.coverage.returned_completed_hour_count == 2
    assert result.coverage.missing_hour_count == 0
    assert not result.coverage.unresolved


@pytest.mark.parametrize("changes, is_sentinel", [
    ({}, True), ({"PeriodTickMs": 3600000}, False),
    ({"PeriodTickMs": False}, False), ({"StartPointUTC": "0"}, False),
    ({"Production": [1]}, False), ({"Consumption": None}, False),
])
def test_only_exact_empty_sentinel_has_special_error(tmp_path, changes, is_sentinel):
    source = {"StartPointUTC": 0.0, "EndPointUTC": 0.0, "PeriodTickMs": 0,
              "Production": [], "Consumption": []}
    source.update(changes)
    path = tmp_path / "empty.json"
    Archive(path).write(source)
    with pytest.raises(ValueError) as exc:
        normalize_archive(path, fetched_at=datetime(2026, 10, 8, tzinfo=timezone.utc),
                          output_dir=tmp_path / "outputs")
    assert isinstance(exc.value, EmptyStatnettResponse) == is_sentinel
    assert not (tmp_path / "outputs").exists()


def test_bounded_response_outside_request_is_rejected(tmp_path):
    start = pd.Timestamp("2025-01-01", tz="Europe/Oslo").tz_convert("UTC")
    path = tmp_path / "outside.json"
    Archive(path).write({
        "StartPointUTC": int((start - pd.Timedelta(hours=1)).timestamp() * 1000),
        "EndPointUTC": int(start.timestamp() * 1000), "PeriodTickMs": 3600000,
        "Production": [1, 2], "Consumption": [3, 4],
    })
    original = path.read_bytes()
    with pytest.raises(ValueError, match="outside the requested date range"):
        normalize_archive(
            path, fetched_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
            output_dir=tmp_path / "outputs", requested_range=DateRange(date(2025, 1, 1), date(2025, 1, 1)),
        )
    assert path.read_bytes() == original
    assert not (tmp_path / "outputs").exists()