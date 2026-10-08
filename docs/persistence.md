# PostgreSQL Storage

PostgreSQL storage is **implemented and optional**. It records completed hourly observations, the snapshots they came from, and changes observed during later fetches. Fetching, normalization, and replay do not require a database unless `--persist` is supplied.

Storing an observation does not approve it for training. All ingestion runs retain `training_ready: false`. See the [pipeline guide](pipeline.md) for normalization and the [Statnett investigation](statnett-source-investigation.md) for unresolved source questions.

## Local Setup

Install the project as described in the [README](../README.md). To use the bundled PostgreSQL 18 development service, you also need Docker with Compose:

```bash
docker compose up -d postgres
python -m oslo_energy.database.migrate
```

Wait until PostgreSQL accepts connections before running the migration. If startup is still in progress, retry the migration once the service is ready.

The initial [migration](../src/oslo_energy/database/migrations/001_statnett_hourly_candidates.sql) adds the current Statnett tables and a schema-version ledger. The [capture identity migration](../src/oslo_energy/database/migrations/002_statnett_capture_identity.sql) allows identical content captured at different fetch times. Run the migration command again before using the updated collector. Existing rows remain; neither migration resets the database or modifies legacy tables. Do not delete an existing Docker volume to apply them.

Fetch, normalize, and store data:

```bash
python -m oslo_energy.pipeline.run_ingestion --from-date 2005-01-01 --normalize --persist
```

The command reports the database run ID, candidate count, and number of current rows inserted or updated. Without `--persist`, no database write is attempted.

## Connection Settings

The [connection module](../src/oslo_energy/database/connection.py) reads these environment variables:

| Variable | Local default |
| --- | --- |
| `POSTGRES_HOST` | `localhost` |
| `POSTGRES_PORT` | `5432` |
| `POSTGRES_DB` | `oslo_energy` |
| `POSTGRES_USER` | `oslo_energy` |
| `POSTGRES_PASSWORD` | `oslo_energy_dev` |

These defaults match the bundled [Docker Compose service](../docker-compose.yml). They are **development-only credentials**, not suitable for production.

The Compose service publishes port 5432. If that port is occupied, use a separate database or deliberately adjust the port mapping and set `POSTGRES_PORT` to match. Python connection settings do not automatically reconfigure the Compose service or an existing database volume.

## Store a Replayed Snapshot

Replay uses the archive's original fetch time rather than the time it is added to the database:

```bash
python -m oslo_energy.pipeline.run_ingestion \
  --replay path/to/archive.json \
  --fetched-at 2026-10-06T02:04:08.987326Z \
  --persist
```

Replace the path and timestamp with the values for your hourly archive. Legacy daily snapshots can be normalized but cannot be stored in the hourly tables.

If a database write fails, the transaction is rolled back and the command returns nonzero. The raw archive, CSVs, and quality report remain available. Correct the database problem and replay the archive with its original timestamp; there is no need to fetch a replacement snapshot just to retry storage.

## What Is Stored

| Table | Contents |
| --- | --- |
| `statnett_ingestion_runs` | Archive path and SHA-256, original fetch time, requested start when known, normalizer version, quality report, and candidate counts |
| `statnett_hourly_observations_current` | Current production and consumption for each UTC hour, local date, source position, and first/last seen provenance |
| `statnett_observation_revisions` | Old and new values for observed changes, including transitions to or from missing values, with the associated runs and timestamps |

Raw JSON snapshots remain on disk for replay. Only structurally valid hours ending by the original fetch time enter the observation table; incomplete and future periods do not. Missing measurements use SQL `NULL`, including observations where both metrics are missing.

`period_start_utc` is the unique key for the current national hourly series, so repeated autumn local hours remain distinct. Measurements use `DOUBLE PRECISION`, matching the normalized floating-point path rather than exact decimal storage. Constraints require hour-aligned periods, one-hour duration, and finite nonnegative values when present.

Source measurements are retained as observed. Future cleaning, aggregation, and feature datasets must be separate transformations rather than edits to those measurements.

## Corrections and Retries

The repository compares source fetch times, not replay times:

| Situation | Result |
| --- | --- |
| Newer snapshot, changed values | Audit the change and update the current observation |
| Newer snapshot, unchanged values | Update last-seen provenance without adding a change record |
| Older snapshot | Retain run metadata without replacing newer current values |
| Equal fetch time, different values | Reject the ambiguous update |
| Same archive checksum, original fetch time, and normalizer version | Treat a consistent retry as already persisted |
| Identical archive content at a later fetch time | Record a new capture and apply the normal timestamp-ordering rules |

A retry with an explicitly conflicting requested start date fails; replay can omit that date. Always retain the original fetch timestamp: a different time identifies another capture and changes which hours are complete. Run metadata, revisions, and current observations commit in one transaction. A transaction-scoped advisory lock serializes repository writes to keep the audit and current values consistent.

The [repository implementation](../src/oslo_energy/database/statnett_repository.py) contains the conflict and transaction rules. A new normalized output directory on replay does not itself imply a new database run.

## Limits of the History

`fetched_at` is when this project captured a snapshot. `recorded_at` is when it recorded a database entry. **Neither is a provider publication timestamp.**

Repeated collection can capture changes from that point onward, but cannot reconstruct what Statnett published before the first capture. Historical backtesting must account for that limit: the latest current values may contain revisions unavailable at a past forecast date.

Daily catch-up and periodic reconciliation are available as CLI modes; see [incremental jobs](pipeline.md#incremental-jobs). No schedule is installed. Coverage queries distinguish absent hours from stored nulls and use UTC identity across DST. The daily high-water mark alone does not detect earlier gaps. Storage does not settle units, measurement accuracy, revision finality, or training eligibility.

Reconciliation skips the provider's exact empty-response sentinel without adding observations, retains its raw archive, and records the range as unresolved in a JSON job summary. Valid partial responses are persisted normally but remain unresolved when requested hours or measurements are missing. Job modes return nonzero for unresolved coverage as well as execution failures; a nonzero status does not imply earlier committed chunks were rolled back. See the [pipeline guide](pipeline.md#incremental-jobs) for summary and exit-status rules.

## Database Tests

The ordinary test suite runs without PostgreSQL. To enable integration tests, point the connection settings at a **dedicated development or test database**, then run:

```bash
RUN_POSTGRES_TESTS=1 python -m pytest tests/pipeline/test_statnett_persistence.py -q
```

These tests apply migrations and write test observations, then clean up their records. They exercise retries, corrections, missing-value transitions, stale snapshots, and rollback. Do not run them against production data. CI currently runs only the offline suite.
