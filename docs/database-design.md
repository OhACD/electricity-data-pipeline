# Persistence Design

## Status

**Proposed next stage; not implemented.** PostgreSQL is the planned observation store. There is no active Statnett migration, repository or database write in the CLI. Existing [Docker Compose](../docker-compose.yml) and [connection code](../src/oslo_energy/database/connection.py) are infrastructure for this later stage, not evidence of working persistence.

This plan separates **storing what we observed** from **certifying it for training**. Provider disagreement does not prevent designing a provenance-preserving store; it does prevent claiming independent accuracy. Candidate storage must retain quality status and must not automatically make `training_ready` true.

The [system reference](oslo_energy_data_normalization_cleaning_pipeline.md) owns normalization and eligibility contracts. The [source research](statnett_hourly_source_decision.md) owns the evidence and the choice of hourly API observations as the canonical measurement layer.

## Boundaries

- Raw snapshots remain outside the observation table and are retained for replay and provenance.
- The repository will own SQL, conflict handling and transactions; database constraints enforce stored integrity.
- Initial observation writes will accept structurally valid **completed hourly candidates**, preserving nulls and unresolved validation status. Incomplete/future periods remain outside this observation table.
- Legacy daily responses and CSV exports are references, not alternative hourly rows to mix into this table.
- Cleaning, daily aggregates, features and model datasets derive from the canonical layer and must not overwrite source measurements.

## Proposed Observation Table

Working name: `statnett_electricity_observations`. These are design fields, not executable migration SQL.

| Column | Proposed type | Nullable | Meaning |
| --- | --- | --- | --- |
| `period_start_utc` | `TIMESTAMPTZ` | No | UTC identity for the national hourly series |
| `period_end_utc` | `TIMESTAMPTZ` | No | One elapsed hour after the start |
| `observation_date` | `DATE` | No | Norwegian local date for grouping; not unique |
| `production` | Numeric, precision to be decided | Yes | Unscaled provider value |
| `consumption` | Numeric, precision to be decided | Yes | Unscaled provider value |
| `ingested_at` | `TIMESTAMPTZ` | No | Time the system persisted the observation |

Associated provenance must retain the source snapshot identifier/checksum, original UTC fetch time, normalization contract version and quality report/status. Decide whether those fields belong on observations or in referenced run metadata before implementing the migration. `ingested_at` is persistence time, not measurement time or original fetch time.

## Integrity and Eligibility

Planned constraints:

- Required timezone-aware period, fetch and ingestion timestamps; hour-aligned start and one-hour duration.
- Finite nonnegative measurements, with source missingness stored as SQL `NULL`, never zero or numeric `NaN`.
- Unique `period_start_utc` for a single current-value national hourly series. Extend the key if sources, scopes, resolutions or stored revisions expand.
- Writes limited to periods ending by the original fetch time; keep unresolved quality status visible.

Completed does not mean final or training-ready. Training additionally requires verified units, explicit missing-data treatment and revision/point-in-time rules. Future values and later corrections must not leak information into a historical backtest.

## Revisions and Transactions

Before writes are implemented, choose a correction policy: maintain current values with retained snapshot lineage, or store versioned observations with a revision-aware key. Decide how changed values, newly missing values and identical retries are handled. Do not silently inherit `ON CONFLICT DO NOTHING`; it can hide provider corrections.

Repeated ingestion must be idempotent under that policy. Batch writes must commit atomically; an unrecoverable error rolls back the whole batch. Raw snapshots remain unchanged regardless of database updates.

## Implementation Checklist

1. Decide numeric precision, provenance representation, uniqueness and revision policy.
2. Add a Statnett-specific migration and transactional repository.
3. Add explicit opt-in pipeline persistence without changing raw archival or training status.
4. Test round trips, SQL nulls, DST identities, duplicate retries, corrections, invalid values, rollback and incomplete/future exclusion.

## Development Transition

The former SSB migration and repository are retired. Existing monthly SSB tables and Docker volumes are untouched; do not reinterpret them as Statnett observations. No reset is needed to run the current pipeline. Any deletion must be separately authorized. Development credentials must not be used in production.

Cloud storage, additional indexes and migration frameworks remain deferred until their requirements are concrete. This document should grow with actual storage decisions, not speculative infrastructure.
