# Persistence Design

## Status

**Implemented, opt-in.** A versioned PostgreSQL migration, transactional repository and `--persist` CLI path store completed hourly candidates. Applying the migration and writing rows are explicit; default ingestion and replay remain offline from PostgreSQL. Existing [Docker Compose](../docker-compose.yml) and [connection code](../src/oslo_energy/database/connection.py) are reused. The pre-existing legacy `statnett_electricity_observations` table is not modified; the new current-value table has a distinct name.

This plan separates **storing what we observed** from **certifying it for training**. Provider disagreement does not prevent designing a provenance-preserving store; it does prevent claiming independent accuracy. Candidate storage must retain quality status and must not automatically make `training_ready` true.

The [system reference](oslo_energy_data_normalization_cleaning_pipeline.md) owns normalization and eligibility contracts. The [source research](statnett_hourly_source_decision.md) owns the evidence and the choice of hourly API observations as the canonical measurement layer.

## Boundaries

- Raw snapshots remain outside the observation table and are retained for replay and provenance.
- [StatnettRepository](../src/oslo_energy/database/statnett_repository.py) owns parameterized SQL, conflict handling and transactions; database constraints enforce stored integrity.
- Writes accept structurally valid **completed hourly candidates**, preserving nulls and unresolved validation status. Incomplete/future periods remain outside the current-value table.
- Legacy daily responses and CSV exports are references, not alternative hourly rows to mix into this table.
- Cleaning, daily aggregates, features and model datasets derive from the canonical layer and must not overwrite source measurements.

## Stored Records

Migration: [`001_statnett_hourly_candidates.sql`](../src/oslo_energy/database/migrations/001_statnett_hourly_candidates.sql). It adds three Statnett tables and a schema-version ledger without changing pre-existing tables.

| Table | Purpose |
| --- | --- |
| `statnett_ingestion_runs` | Archive path and SHA-256, original fetch time, requested start (when known), frequency, normalizer version, quality report and candidate counts; `training_ready` is constrained false |
| `statnett_hourly_observations_current` | One current row per UTC hour, Oslo date, source index, nullable values, first/last run and source fetch times, DB record time |
| `statnett_observation_revisions` | Change-only old/new values including NULL transitions, previous/new run, source fetch time and DB record time |

Measurements use `DOUBLE PRECISION`, matching the normalized float64 data path; this is not exact decimal storage. `recorded_at` is database time; `fetched_at` is the system's fetch time, not a provider publication timestamp. Replaying an old archive records its original fetch time and the later database record time separately.

## Integrity and Eligibility

Database and repository constraints:

- Required timezone-aware period, fetch and ingestion timestamps; hour-aligned start and one-hour duration.
- Finite nonnegative measurements, with source missingness stored as SQL `NULL`, never zero or numeric `NaN`.
- Unique `period_start_utc` for the current national series. Extend the key if sources, scopes or resolutions expand.
- Writes limited to periods ending by the original fetch time; `training_ready` remains false.

Completed does not mean final or training-ready. Training additionally requires verified units, explicit missing-data treatment and revision/point-in-time rules. Future values and later corrections must not leak information into a historical backtest.

## Revisions and Transactions

The current row is updated only by a strictly newer fetch timestamp. Older replays are retained as run metadata but cannot roll back current values; different values at an equal fetch timestamp fail as ambiguous. Unchanged newer snapshots update last-seen provenance without an audit row. Changed values, including transitions to or from NULL, are audited before the current row changes.

The same archive checksum and normalizer version are idempotent, and a retry with inconsistent fetch metadata fails. A transaction-scoped advisory lock serializes writes so audit and current values cannot race. Run metadata, revisions and current values commit atomically; a database error rolls the batch back. Raw snapshots and normalized output files remain unchanged.

## Implementation Checklist

1. Apply the migration explicitly: `python -m oslo_energy.database.migrate`.
2. Fetch and persist completed hourly candidates: `python -m oslo_energy.pipeline.run_ingestion --normalize --persist`.
3. Replay and persist with the original cutoff: `python -m oslo_energy.pipeline.run_ingestion --replay PATH --fetched-at ISO_TIMESTAMP --persist`.
4. Run opt-in database tests with `RUN_POSTGRES_TESTS=1 python -m pytest tests/pipeline/test_statnett_persistence.py -q`; the ordinary suite remains offline.

## Development Transition

The former SSB migration and repository are retired. Existing monthly SSB tables and Docker volumes are untouched; do not reinterpret them as Statnett observations. No reset is needed to run the current pipeline. Any deletion must be separately authorized. Development credentials must not be used in production.

Cloud storage, additional indexes and migration frameworks remain deferred until their requirements are concrete. Accuracy, provider revisions before first capture, and leakage-safe historical availability remain unresolved; persistence is not training approval.
