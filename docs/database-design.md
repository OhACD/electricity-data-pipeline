# Database Design

## Status

This is a proposed design for the next Statnett phase, not an implemented schema. Current ingestion archives raw JSON and can normalize daily candidates into CSVs and a quality report. There is no active migration, observation repository, or database write in the command-line pipeline. Persistence and training remain blocked pending reconciliation of daily API values with the official hourly export.

PostgreSQL remains the recommended database. We define our own tables and constraints inside PostgreSQL; building a database engine or introducing a data lake is unnecessary for this starter time series.

The existing Docker Compose service and `src/oslo_energy/database/connection.py` remain available for future persistence. Database integration tests will return when a Statnett schema and repository exist.

## Boundaries

```text
Statnett -> raw JSON archive
                  |
                  v  (implemented, opt-in/replay)
             normalization
                  |
                  v
       structure/value validation
                  |
                  v
     completed/current/future candidates
                  |
              v  (future; reference validation required)
             PostgreSQL
                  |
                  v  (later analytical phases)
        quality analysis / cleaning
                  |
                  v
       feature engineering / models
```

The API client interprets HTTP, not observation semantics. The normalizer maps validated daily slots to Norwegian dates and removes only verified null DST padding. A future repository will own SQL and transactions, and the database will enforce durable constraints.

Raw responses stay outside the normalized table so transformation changes can be replayed without fetching the source again. Analytical cleaning must not overwrite source observations with imputed or model-specific values.

## Proposed Observation Table

The working name is `statnett_electricity_observations`.

| Column | Proposed type | Nullable | Meaning |
| --- | --- | --- | --- |
| `observation_date` | `DATE` | No | Norwegian calendar-day identity for the national daily series |
| `production` | Numeric, representation to be decided | Yes | Provider production value; units/aggregation must be verified |
| `consumption` | Numeric, representation to be decided | Yes | Provider consumption value; units/aggregation must be verified |
| `ingested_at` | `TIMESTAMPTZ` | No | Time the system persisted the observation |

This is not executable migration SQL. Before choosing types and keys, verify:

- Whether the series describes energy totals or power measurements, and in which units.
- National geographic scope is documented; do not carry over NO1 or consumer-group codes from the former SSB dataset.
- Resolve why daily API values differ from sums of timestamped hourly exports; do not relabel values as MWh before that check passes.
- Keep the implemented date/slot/DST contract under regression tests, including short requests that may return hourly data.
- Whether one date uniquely identifies an observation for the endpoint and resolution.
- Precision and how corrected historical values should be handled.

The daily normalizer uses `Europe/Oslo` calendar dates as identity. Derive UTC period boundaries from independently localized consecutive midnights; days can span 23, 24, or 25 hours. UTC conversion is lossless, but adding fixed 24-hour intervals to a UTC start is not a calendar-day mapping. Persist optional UTC boundaries only if querying requirements justify them. An hourly dataset would instead use canonical UTC instants with a separate identity contract.

## Integrity and Missing Values

Planned constraints:

- Required Norwegian observation date and UTC ingestion timestamp.
- Nonnegative production and consumption for nonmissing measurements.
- Missing source measurements stored as SQL `NULL`, never zero or PostgreSQL numeric `NaN`.
- A unique observation key, initially expected to be `observation_date` for the single national daily series. Revisit before adding overlapping sources or resolutions.

Missing values are different from missing observation periods. Preserve the former and report both. Normalization will fail when source structure cannot be interpreted safely; it will not silently truncate mismatched arrays or fabricate timestamps.

## Historical Data and Leakage

Only observations classified as historical using verified provider semantics may enter the historical table. Raw archives may contain forecasts, because preserving the response is different from approving records for training.

The earlier design's 21-period forecast-tail assumption was not supported by the full archive audit: its 21 surplus slots are null padding after the 21 autumn DST days from 2005 through 2025. Do not remove the final 21 measurements or treat every null as padding.

Normalization separates dates before, equal to, and after the original fetch date in Norway. Completed-day candidates exclude current incomplete data and future quarantine, but this is not proof of source finality or absence of historical forecasts/revisions. Quality reports keep `training_ready: false`; persistence must wait for independent value/unit and eligibility validation.

Separating forecasts is necessary but does not prove point-in-time training validity: historical revisions may also contain information unavailable at an earlier prediction time. Snapshot provenance and revision handling need to be considered before backtesting models.

## Idempotency and Transactions

Repeated persistence must not create duplicate observations. The repository will use the verified uniqueness key with an explicit conflict policy.

Do not inherit `ON CONFLICT DO NOTHING` automatically: Statnett may correct historical values. Decide whether to update a current observation or retain revisions, and what `ingested_at` means after an update, before implementing writes.

Batch persistence should commit atomically. Any unrecoverable insert error should roll back the batch, preserving the former pipeline's transactional guarantee without retaining its SSB-specific table.

Integration tests must cover missing values, duplicate protection, correction policy, negative-value rejection, rollback, timezone-aware timestamps, and forecast exclusion.

## Development Transition

The scaffold cleanup retires the old SSB migration and repository; it does not execute database changes. An existing `electricity_observations` table contains monthly SSB data and must not be reinterpreted as Statnett measurements.

Existing starter data has been declared disposable. Removing it is optional and must be explicit. The following command **permanently deletes the old table and its contents** from the local development database; do not run it against a database whose data you need:

```bash
docker compose exec -T postgres psql -U oslo_energy -d oslo_energy \
  -c 'DROP TABLE IF EXISTS electricity_observations;'
```

No reset is required to run fetch-and-archive ingestion. A new Statnett migration will be added with normalization and persistence, once the contract above is verified. Do not apply the retired migration.

## Deferred Infrastructure

Dedicated ingestion-run tables, revision/provenance tables, additional indexes, cloud object storage, and migration frameworks should be introduced when their requirements are concrete. Feature tables and model outputs must remain separate from the underlying source observations.