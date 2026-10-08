"""Verify raw ingestion provenance, archive isolation, and CLI failure handling."""

import subprocess
import sys
from datetime import date, datetime, timezone

import httpx
import pytest

from oslo_energy.ingestion.archive import Archive
from oslo_energy.ingestion.statnett_client import StatnettClient, StatnettClientError
from oslo_energy.pipeline import run_ingestion
from oslo_energy.pipeline.ingestion import StatnettIngestion


def make_client(payload):
    """Return a client whose mock transport responds with the supplied JSON payload."""
    return StatnettClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    )


def test_ingestion_archives_raw_response(tmp_path):
    """Ingestion preserves raw data and records the requested date and UTC fetch time."""
    payload = {"Production": [None, 12.5], "Consumption": [10.0, None], "metadata": "raw"}

    result = StatnettIngestion(make_client(payload), tmp_path).run(date(2005, 1, 1))

    assert result.from_date == date(2005, 1, 1)
    assert result.fetched_at.tzinfo == timezone.utc
    assert result.archive_path.parent == tmp_path
    assert result.archive_path.name.startswith("statnett_from_2005-01-01_")
    assert Archive(result.archive_path).read() == payload


def test_repeated_ingestion_creates_distinct_archives(tmp_path, monkeypatch):
    """Repeated ingestion creates distinct intact archives even at the same fetch time."""
    class FixedClock:
        """Supply a constant fetch time to expose archive filename collisions."""

        @staticmethod
        def now(zone):
            """Return midnight on January 1, 2026 in the requested timezone."""
            return datetime(2026, 1, 1, tzinfo=zone)

    monkeypatch.setattr("oslo_energy.pipeline.ingestion.datetime", FixedClock)
    ingestion = StatnettIngestion(make_client({"raw": True}), tmp_path)

    first = ingestion.run(date(2005, 1, 1))
    second = ingestion.run(date(2005, 1, 1))

    assert first.archive_path != second.archive_path
    assert Archive(first.archive_path).read() == {"raw": True}
    assert Archive(second.archive_path).read() == {"raw": True}


def test_fetch_failure_does_not_create_archive(tmp_path):
    """A failed fetch propagates the client error without creating the archive directory."""
    client = StatnettClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500))
    )

    with pytest.raises(StatnettClientError):
        StatnettIngestion(client, tmp_path / "raw").run(date(2005, 1, 1))

    assert not (tmp_path / "raw").exists()


def test_archive_failure_propagates(tmp_path):
    """Ingestion propagates a filesystem error from an invalid archive directory."""
    path = tmp_path / "not-a-directory"
    path.write_text("existing file", encoding="utf-8")

    with pytest.raises(OSError):
        StatnettIngestion(make_client({}), path).run(date(2005, 1, 1))


@pytest.mark.parametrize("start_date", [None, "2025-01-01"])
def test_cli_defaults_and_explicit_start_date(tmp_path, monkeypatch, capsys, start_date):
    """The CLI requests the default or explicit Oslo start date and reports its archive."""
    requested_dates = []

    def respond(request):
        """Record the UTC start bound, assert hourly frequency, and return raw data."""
        requested_dates.append(
            datetime.fromtimestamp(int(request.url.params["FromInTicks"]) / 1000, timezone.utc)
        )
        assert request.url.params["Frequency"] == "Hours"
        return httpx.Response(200, json={"raw": True})

    monkeypatch.setattr(
        run_ingestion,
        "StatnettClient",
        lambda: StatnettClient(transport=httpx.MockTransport(respond)),
    )
    monkeypatch.chdir(tmp_path)
    args = [] if start_date is None else ["--from-date", start_date]

    assert run_ingestion.main(args) == 0

    expected_date = start_date or "2005-01-01"
    from zoneinfo import ZoneInfo

    assert [value.astimezone(ZoneInfo("Europe/Oslo")).date().isoformat() for value in requested_dates] == [expected_date]
    assert len(list((tmp_path / "data/raw/statnett").glob("*.json"))) == 1
    output = capsys.readouterr()
    assert expected_date in output.out
    assert "Archived raw response" in output.out
    assert not output.err


def test_cli_custom_archive_dir(tmp_path, monkeypatch):
    """The CLI writes one archive into the explicitly selected directory."""
    monkeypatch.setattr(run_ingestion, "StatnettClient", lambda: make_client({}))
    archive_dir = tmp_path / "custom"

    assert run_ingestion.main(["--archive-dir", str(archive_dir)]) == 0
    assert len(list(archive_dir.glob("*.json"))) == 1


@pytest.mark.parametrize("value", ["bad", "2025-02-30", "20250101", "2025-1-1"])
def test_cli_invalid_date_fails_before_request(monkeypatch, value):
    """Invalid start dates cause parser exit code 2 before client construction."""
    def unexpected_client():
        """Fail if invalid date arguments reach client construction."""
        pytest.fail("Invalid dates must fail before creating the client")

    monkeypatch.setattr(run_ingestion, "StatnettClient", unexpected_client)

    with pytest.raises(SystemExit) as exc:
        run_ingestion.main(["--from-date", value])

    assert exc.value.code == 2


def test_cli_api_failure_returns_nonzero(tmp_path, monkeypatch, capsys):
    """An API failure returns code 1, reports stderr, and leaves no archive."""
    monkeypatch.setattr(
        run_ingestion,
        "StatnettClient",
        lambda: StatnettClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(500))
        ),
    )

    assert run_ingestion.main(["--archive-dir", str(tmp_path)]) == 1
    output = capsys.readouterr()
    assert "Ingestion failed" in output.err
    assert not output.out
    assert not list(tmp_path.iterdir())


def test_cli_archive_failure_returns_nonzero(tmp_path, monkeypatch, capsys):
    """An archive filesystem failure returns code 1 and reports ingestion failure."""
    monkeypatch.setattr(run_ingestion, "StatnettClient", lambda: make_client({}))
    path = tmp_path / "not-a-directory"
    path.write_text("existing file", encoding="utf-8")

    assert run_ingestion.main(["--archive-dir", str(path)]) == 1
    assert "Ingestion failed" in capsys.readouterr().err


def test_raw_cli_does_not_import_database():
    """Importing the raw ingestion CLI in a fresh interpreter loads no database modules."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import oslo_energy.pipeline.run_ingestion; "
            "assert not any(name.startswith('oslo_energy.database') for name in sys.modules)",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr