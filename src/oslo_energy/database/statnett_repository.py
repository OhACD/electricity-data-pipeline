"""Transactional persistence for completed hourly Statnett candidates."""

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import math
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from psycopg.types.json import Jsonb

from oslo_energy.database.connection import create_connection, load_config
from oslo_energy.transformation.statnett_normalizer import NormalizedStatnettData


NORMALIZER_VERSION = "statnett-normalizer-v1"
_CANDIDATE_COLUMNS = (
    "observation_date", "period_start_utc", "period_end_utc", "period_hours",
    "source_index", "production", "consumption",
)


class PersistenceError(OSError):
    """Raised when a Statnett candidate batch cannot be committed."""


@dataclass(frozen=True)
class PersistenceResult:
    run_id: int
    candidate_count: int
    inserted_or_updated_count: int
    idempotent: bool


def _utc_datetime(value: Any, column: str) -> datetime:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        raise ValueError(f"{column} must be timezone-aware")
    return timestamp.to_pydatetime().astimezone(timezone.utc)


def _measurement(value: Any, column: str) -> float | None:
    if pd.isna(value):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{column} must be a finite nonnegative number or null")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{column} must be a finite nonnegative number or null")
    return result


def _prepare_candidates(data: NormalizedStatnettData, fetched_at: datetime) -> list[tuple]:
    quality = data.quality
    if quality.frequency != "hourly" or quality.period_tick_ms != 3_600_000:
        raise ValueError("Only normalized hourly Statnett candidates can be persisted")
    if quality.training_ready:
        raise ValueError("Statnett candidates must remain blocked from training")
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise ValueError("fetched_at must be timezone-aware")
    fetched_at = fetched_at.astimezone(timezone.utc)
    frame = data.historical
    missing_columns = set(_CANDIDATE_COLUMNS).difference(frame.columns)
    if missing_columns:
        raise ValueError(f"Candidate data is missing columns: {sorted(missing_columns)}")

    candidates = []
    seen = set()
    for row in frame.loc[:, _CANDIDATE_COLUMNS].itertuples(index=False, name=None):
        local_date, raw_start, raw_end, hours, source_index, production, consumption = row
        start = _utc_datetime(raw_start, "period_start_utc")
        end = _utc_datetime(raw_end, "period_end_utc")
        if start.minute or start.second or start.microsecond:
            raise ValueError("period_start_utc must be aligned to a UTC hour")
        if end - start != timedelta(hours=1) or end > fetched_at:
            raise ValueError("Only completed one-hour periods can be persisted")
        if hours != 1 or start in seen:
            raise ValueError("Candidate periods must be unique one-hour UTC intervals")
        seen.add(start)
        if pd.Timestamp(start).tz_convert("Europe/Oslo").date() != pd.Timestamp(local_date).date():
            raise ValueError("observation_date must match the Europe/Oslo period date")
        if isinstance(source_index, bool) or int(source_index) < 0:
            raise ValueError("source_index must be a nonnegative integer")
        candidates.append((
            start, end, pd.Timestamp(local_date).date(), int(source_index),
            _measurement(production, "production"),
            _measurement(consumption, "consumption"), fetched_at,
        ))
    return candidates


class StatnettRepository:
    def __init__(self, connection_factory: Callable[[], Any] | None = None):
        self._connection_factory = connection_factory or (
            lambda: create_connection(load_config())
        )

    def persist(
        self,
        data: NormalizedStatnettData,
        *,
        archive_path: Path,
        fetched_at: datetime,
        requested_from_date: date | None = None,
    ) -> PersistenceResult:
        if not archive_path.is_file():
            raise ValueError(f"Raw archive does not exist: {archive_path}")
        candidates = _prepare_candidates(data, fetched_at)
        archive_sha256 = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        fetched_at = fetched_at.astimezone(timezone.utc)
        try:
            with self._connection_factory() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_xact_lock(%s)", (614782194,))
                    cursor.execute(
                        "SELECT id, fetched_at, requested_from_date, persisted_count "
                        "FROM statnett_ingestion_runs "
                        "WHERE archive_sha256 = %s AND normalizer_version = %s",
                        (archive_sha256, NORMALIZER_VERSION),
                    )
                    existing = cursor.fetchone()
                    if existing is not None:
                        run_id, saved_fetch, saved_from, _persisted_count = existing
                        if saved_fetch != fetched_at or saved_from != requested_from_date:
                            raise ValueError(
                                "This archive and normalizer version were already persisted "
                                "with different fetch metadata"
                            )
                        return PersistenceResult(run_id, len(candidates), 0, True)

                    cursor.execute(
                        "INSERT INTO statnett_ingestion_runs "
                        "(requested_from_date, fetched_at, archive_path, archive_sha256, "
                        "frequency, period_tick_ms, normalizer_version, quality_report, "
                        "candidate_count, persisted_count) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                        (
                            requested_from_date, fetched_at, str(archive_path.resolve()),
                            archive_sha256, data.quality.frequency, data.quality.period_tick_ms,
                            NORMALIZER_VERSION, Jsonb(asdict(data.quality)), len(candidates),
                            len(candidates),
                        ),
                    )
                    run_id = cursor.fetchone()[0]
                    inserted_or_updated_count = 0
                    cursor.execute(
                        "CREATE TEMP TABLE statnett_candidate_stage ("
                        "period_start_utc TIMESTAMPTZ PRIMARY KEY, period_end_utc TIMESTAMPTZ NOT NULL, "
                        "observation_date DATE NOT NULL, source_index INTEGER NOT NULL, "
                        "production DOUBLE PRECISION, consumption DOUBLE PRECISION, "
                        "fetched_at TIMESTAMPTZ NOT NULL) ON COMMIT DROP"
                    )
                    if candidates:
                        with cursor.copy(
                            "COPY statnett_candidate_stage (period_start_utc, period_end_utc, "
                            "observation_date, source_index, production, consumption, fetched_at) FROM STDIN"
                        ) as copy:
                            for candidate in candidates:
                                copy.write_row(candidate)

                        cursor.execute(
                            "SELECT 1 FROM statnett_hourly_observations_current AS current "
                            "JOIN statnett_candidate_stage AS stage USING (period_start_utc) "
                            "WHERE stage.fetched_at = current.last_seen_fetch_at "
                            "AND (current.production IS DISTINCT FROM stage.production "
                            "OR current.consumption IS DISTINCT FROM stage.consumption) LIMIT 1"
                        )
                        if cursor.fetchone() is not None:
                            raise ValueError(
                                "Different values for an observation have the same fetch timestamp; "
                                "their order is ambiguous"
                            )

                        cursor.execute(
                            "INSERT INTO statnett_observation_revisions "
                            "(period_start_utc, previous_run_id, new_run_id, old_production, "
                            "new_production, old_consumption, new_consumption, source_fetched_at) "
                            "SELECT current.period_start_utc, current.last_seen_run_id, %s, "
                            "current.production, stage.production, current.consumption, stage.consumption, "
                            "stage.fetched_at FROM statnett_hourly_observations_current AS current "
                            "JOIN statnett_candidate_stage AS stage USING (period_start_utc) "
                            "WHERE stage.fetched_at > current.last_seen_fetch_at "
                            "AND (current.production IS DISTINCT FROM stage.production "
                            "OR current.consumption IS DISTINCT FROM stage.consumption)",
                            (run_id,),
                        )
                        cursor.execute(
                            "INSERT INTO statnett_hourly_observations_current "
                            "(period_start_utc, period_end_utc, observation_date, source_index, "
                            "production, consumption, first_seen_run_id, last_seen_run_id, "
                            "first_seen_fetch_at, last_seen_fetch_at) "
                            "SELECT period_start_utc, period_end_utc, observation_date, source_index, "
                            "production, consumption, %s, %s, fetched_at, fetched_at "
                            "FROM statnett_candidate_stage "
                            "ON CONFLICT (period_start_utc) DO UPDATE SET "
                            "period_end_utc = EXCLUDED.period_end_utc, "
                            "observation_date = EXCLUDED.observation_date, "
                            "source_index = EXCLUDED.source_index, production = EXCLUDED.production, "
                            "consumption = EXCLUDED.consumption, last_seen_run_id = EXCLUDED.last_seen_run_id, "
                            "last_seen_fetch_at = EXCLUDED.last_seen_fetch_at, recorded_at = now() "
                            "WHERE EXCLUDED.last_seen_fetch_at > statnett_hourly_observations_current.last_seen_fetch_at",
                            (run_id, run_id),
                        )
                        inserted_or_updated_count = cursor.rowcount
        except ValueError:
            raise
        except Exception as exc:
            raise PersistenceError(f"Statnett candidate transaction rolled back: {exc}") from exc
        return PersistenceResult(run_id, len(candidates), inserted_or_updated_count, False)