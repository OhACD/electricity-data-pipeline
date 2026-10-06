from datetime import datetime, timedelta, timezone
import hashlib
import os
from uuid import uuid4

import pandas as pd
import pytest

from oslo_energy.database.connection import create_connection, load_config
from oslo_energy.database.migrate import apply_migrations
from oslo_energy.database import statnett_repository
from oslo_energy.database.statnett_repository import (
    PersistenceError,
    PersistenceResult,
    StatnettRepository,
    _prepare_candidates,
)
from oslo_energy.ingestion.archive import Archive
from oslo_energy.pipeline import run_ingestion
from oslo_energy.transformation.statnett_normalizer import StatnettNormalizer


def normalized_hour(start, fetched_at, production, consumption):
    timestamp = int(start.timestamp() * 1000)
    source = {
        "StartPointUTC": timestamp,
        "EndPointUTC": timestamp,
        "PeriodTickMs": 3600000,
        "Production": [production],
        "Consumption": [consumption],
    }
    return StatnettNormalizer().normalize(source, fetched_at=fetched_at)


def test_candidate_preparation_preserves_nulls_and_repeated_dst_hour():
    fetched_at = datetime(2025, 10, 26, 3, tzinfo=timezone.utc)
    start = datetime(2025, 10, 26, 0, tzinfo=timezone.utc)
    source = {
        "StartPointUTC": int(start.timestamp() * 1000),
        "EndPointUTC": int((start + timedelta(hours=1)).timestamp() * 1000),
        "PeriodTickMs": 3600000,
        "Production": [None, 2],
        "Consumption": [1, None],
    }
    data = StatnettNormalizer().normalize(source, fetched_at=fetched_at)

    candidates = _prepare_candidates(data, fetched_at)

    assert len(candidates) == 2
    assert candidates[0][0] != candidates[1][0]
    assert candidates[0][2] == candidates[1][2]
    assert candidates[0][4:6] == (None, 1.0)
    assert candidates[1][4:6] == (2.0, None)


def test_candidate_preparation_rejects_legacy_daily_data():
    fetched_at = datetime(2025, 1, 4, tzinfo=timezone.utc)
    midnight = pd.Timestamp("2025-01-01", tz="Europe/Oslo")
    daily_source = {
        "StartPointUTC": int(midnight.timestamp() * 1000),
        "EndPointUTC": int(midnight.timestamp() * 1000),
        "PeriodTickMs": 86400000,
        "Production": [1],
        "Consumption": [2],
    }
    data = StatnettNormalizer().normalize(daily_source, fetched_at=fetched_at)

    with pytest.raises(ValueError, match="hourly"):
        _prepare_candidates(data, fetched_at)


def test_cli_persist_is_opt_in_and_live_requires_normalization(tmp_path, monkeypatch):
    def unexpected_client():
        pytest.fail("Persistence argument validation must happen before fetching")

    monkeypatch.setattr(run_ingestion, "StatnettClient", unexpected_client)
    with pytest.raises(SystemExit) as exc:
        run_ingestion.main(["--persist", "--archive-dir", str(tmp_path / "raw")])
    assert exc.value.code == 2
    assert not (tmp_path / "raw").exists()


def test_cli_replay_persistence_uses_archive_cutoff_and_keeps_artifacts(tmp_path, monkeypatch):
    fetched_at = datetime(2025, 1, 1, 2, tzinfo=timezone.utc)
    source = {
        "StartPointUTC": int(datetime(2025, 1, 1, 0, tzinfo=timezone.utc).timestamp() * 1000),
        "EndPointUTC": int(datetime(2025, 1, 1, 0, tzinfo=timezone.utc).timestamp() * 1000),
        "PeriodTickMs": 3600000,
        "Production": [None],
        "Consumption": [5],
    }
    archive_path = tmp_path / "source.json"
    Archive(archive_path).write(source)
    calls = []

    class Repository:
        def persist(self, data, **kwargs):
            calls.append((data, kwargs))
            return PersistenceResult(7, 1, 1, False)

    monkeypatch.setattr(statnett_repository, "StatnettRepository", Repository)
    output_dir = tmp_path / "normalized"
    assert run_ingestion.main([
        "--replay", str(archive_path), "--fetched-at", fetched_at.isoformat(),
        "--persist", "--output-dir", str(output_dir),
    ]) == 0
    assert len(calls) == 1
    assert calls[0][1]["fetched_at"] == fetched_at
    assert calls[0][1]["requested_from_date"] is None
    assert calls[0][1]["archive_path"] == archive_path
    assert list(output_dir.glob("*/quality.json"))


def test_cli_database_failure_keeps_raw_and_normalized_artifacts(tmp_path, monkeypatch, capsys):
    fetched_at = datetime(2025, 1, 1, 2, tzinfo=timezone.utc)
    start = datetime(2025, 1, 1, 0, tzinfo=timezone.utc)
    timestamp = int(start.timestamp() * 1000)
    archive_path = tmp_path / "source.json"
    Archive(archive_path).write({
        "StartPointUTC": timestamp,
        "EndPointUTC": timestamp,
        "PeriodTickMs": 3600000,
        "Production": [1],
        "Consumption": [2],
    })
    original = archive_path.read_bytes()

    class Repository:
        def persist(self, data, **kwargs):
            raise PersistenceError("simulated unavailable database")

    monkeypatch.setattr(statnett_repository, "StatnettRepository", Repository)
    output_dir = tmp_path / "normalized"
    assert run_ingestion.main([
        "--replay", str(archive_path), "--fetched-at", fetched_at.isoformat(),
        "--persist", "--output-dir", str(output_dir),
    ]) == 1
    assert archive_path.read_bytes() == original
    assert list(output_dir.glob("*/quality.json"))
    assert "simulated unavailable database" in capsys.readouterr().err


@pytest.fixture
def postgres_database():
    if os.getenv("RUN_POSTGRES_TESTS") != "1":
        pytest.skip("Set RUN_POSTGRES_TESTS=1 to enable PostgreSQL integration tests")
    apply_migrations()
    yield StatnettRepository()


def new_test_period():
    return datetime(2200, 1, 1, tzinfo=timezone.utc) + timedelta(hours=uuid4().int % 100000)


def cleanup_test_runs(checksums):
    if not checksums:
        return
    with create_connection(load_config()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM statnett_ingestion_runs WHERE archive_sha256 = ANY(%s)",
                (checksums,),
            )
            run_ids = [row[0] for row in cursor.fetchall()]
            if not run_ids:
                return
            cursor.execute(
                "DELETE FROM statnett_observation_revisions "
                "WHERE previous_run_id = ANY(%s) OR new_run_id = ANY(%s)",
                (run_ids, run_ids),
            )
            cursor.execute(
                "DELETE FROM statnett_hourly_observations_current "
                "WHERE first_seen_run_id = ANY(%s) OR last_seen_run_id = ANY(%s)",
                (run_ids, run_ids),
            )
            cursor.execute("DELETE FROM statnett_ingestion_runs WHERE id = ANY(%s)", (run_ids,))


@pytest.mark.postgres
def test_postgres_retries_corrections_nulls_and_stale_snapshots(postgres_database, tmp_path):
    start = new_test_period()
    fetched = start + timedelta(hours=2)
    paths = []
    checksums = []

    def persist(name, production, consumption, fetched_at):
        path = tmp_path / f"{name}.json"
        Archive(path).write({"snapshot": name})
        paths.append(path)
        checksums.append(hashlib.sha256(path.read_bytes()).hexdigest())
        data = normalized_hour(start, fetched_at, production, consumption)
        return postgres_database.persist(data, archive_path=path, fetched_at=fetched_at)

    try:
        first = persist("first", 1, None, fetched)
        retry = postgres_database.persist(
            normalized_hour(start, fetched, 1, None), archive_path=paths[0], fetched_at=fetched
        )
        assert retry.idempotent and retry.run_id == first.run_id
        assert retry.inserted_or_updated_count == 0

        corrected = persist("corrected", 2, 3, fetched + timedelta(hours=1))
        with pytest.raises(ValueError, match="same fetch timestamp"):
            persist("same-time-conflict", 8, 8, fetched + timedelta(hours=1))
        stale = persist("stale", 9, 8, fetched - timedelta(hours=1))

        with create_connection(load_config()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT production, consumption, first_seen_run_id, last_seen_run_id, "
                    "first_seen_fetch_at, last_seen_fetch_at "
                    "FROM statnett_hourly_observations_current WHERE period_start_utc = %s",
                    (start,),
                )
                current = cursor.fetchone()
                assert current == (
                    2.0, 3.0, first.run_id, corrected.run_id, fetched, fetched + timedelta(hours=1)
                )
                cursor.execute(
                    "SELECT old_consumption, new_consumption, previous_run_id, new_run_id "
                    "FROM statnett_observation_revisions WHERE period_start_utc = %s",
                    (start,),
                )
                assert cursor.fetchall() == [(None, 3.0, first.run_id, corrected.run_id)]
        assert stale.inserted_or_updated_count == 0
    finally:
        cleanup_test_runs(checksums)


@pytest.mark.postgres
def test_postgres_batch_rolls_back_run_and_observations(postgres_database, tmp_path):
    start = new_test_period()
    fetched = start + timedelta(hours=2)
    path = tmp_path / "rollback.json"
    Archive(path).write({"snapshot": "rollback"})
    data = normalized_hour(start, fetched, 1, 2)
    data.historical.loc[data.historical.index[0], "source_index"] = 2**40
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()

    try:
        with pytest.raises(PersistenceError):
            postgres_database.persist(data, archive_path=path, fetched_at=fetched)
        with create_connection(load_config()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM statnett_ingestion_runs WHERE archive_sha256 = %s",
                    (checksum,),
                )
                assert cursor.fetchone()[0] == 0
                cursor.execute(
                    "SELECT count(*) FROM statnett_hourly_observations_current WHERE period_start_utc = %s",
                    (start,),
                )
                assert cursor.fetchone()[0] == 0
    finally:
        cleanup_test_runs([checksum])