# Oslo Energy

A hands-on electricity data pipeline for learning and future consumption forecasting. Statnett is the primary data source. The pipeline archives production/consumption responses and can normalize the daily series into Norwegian calendar-date candidates with a quality report.

The project name comes from its original Oslo-focused goal. Statnett's documented production/consumption coverage is Norway-wide, not municipality-level or NO1-specific. Daily values currently retain provider units because the daily API totals have not been reconciled with the official hourly export.

## Current Scope

```text
Statnett API -> StatnettClient -> StatnettIngestion -> Raw JSON archive
              |
              v  (opt-in or replay)
            StatnettNormalizer
              |
              v
            Daily candidates + quality report
```

Implemented:

- HTTP requests with timeouts and explicit request/response errors.
- A command-line start date and archive directory.
- Raw decoded JSON preservation, including provider metadata and missing values.
- Unique archive filenames and refusal to overwrite an existing file.
- Calendar-aware daily normalization, preserving genuine missing measurements.
- Verified autumn null-padding handling and 23/24/25-hour UTC period boundaries.
- Separate completed-day candidates, incomplete current-day values, and future quarantine.
- Reproducible archive replay with original fetch-time provenance.
- Offline client, archive, normalizer, orchestration, and CLI tests.

PostgreSQL persistence, analytical cleaning, feature engineering, and machine learning are not implemented yet. Outputs are not training-ready: the quality report records `training_ready: false` until daily aggregation/units and source finality are independently verified. A completed date alone does not prove a measurement was final or available at a historical prediction time.

## Quickstart

Use Python 3.10 or newer and run these commands from the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest
```

Fetch and archive the response with a requested start date of 2005-01-01:

```bash
python -m oslo_energy.pipeline.run_ingestion
```

Choose a different start date or archive directory:

```bash
python -m oslo_energy.pipeline.run_ingestion \
  --from-date 2025-01-01 \
  --archive-dir data/raw/statnett
```

Inspect available options:

```bash
python -m oslo_energy.pipeline.run_ingestion --help
```

Fetch, archive, and normalize a daily series:

```bash
python -m oslo_energy.pipeline.run_ingestion --from-date 2005-01-01 --normalize
```

Replay an existing archive without a network request:

```bash
python -m oslo_energy.pipeline.run_ingestion \
  --replay path/to/raw.json \
  --fetched-at 2026-10-06T00:56:03.832107Z \
  --output-dir data/normalized/statnett
```

Replace the example path and fetch timestamp with those of the original download. Replay requires a timezone-aware original fetch time, not the time of replay; otherwise current-day exclusion would change. The source archive filename contains its UTC fetch time. Normalization supports only responses with `PeriodTickMs = 86400000`; shorter requests may return hourly data and must not be interpreted as daily observations.

The start date must use `YYYY-MM-DD`. Relative archive paths are resolved from the working directory. A fetch/decode/archive failure returns a nonzero exit status; a successful run prints the requested date and archive path.

The client calls `GET https://driftsdata.statnett.no/restapi/ProductionConsumption/GetData` with the `From` query parameter. Requesting 2005 does not prove complete historical coverage: the returned metadata and arrays must be checked in the normalization phase. No end-date parameter is exposed.

No Docker service or database is needed for this milestone.

## Raw Archives

New responses are written to ignored `data/raw/statnett/` by default. Filenames include the requested start date, UTC fetch time, and a unique identifier. Repeated runs create separate snapshots rather than replacing earlier responses.

Archives contain the decoded provider JSON, without added envelopes, filtering, interpolation, or timestamp changes. They are not byte-for-byte copies of the original HTTP response: whitespace and JSON formatting are regenerated. A response containing invalid JSON or a non-object top-level value fails before archival. Invalid non-finite numeric values cannot be written as JSON.

The existing responses are retained under [data/examples/statnett/](data/examples/statnett/) for inspection and future replay. They are examples, not proof of data completeness or a verified forecast boundary. Their interval metadata currently indicates 86,400,000 milliseconds (daily).

A live audit on 2026-10-06 found 7,970 raw slots per array covering 7,949 Norwegian dates, with 21 null padding slots immediately after autumn DST days. These are historical padding, not evidence of 21 forecast days. Normalization retains the remaining 10 missing values per series, separates 7,948 completed-day candidates from one incomplete date, and does not invent future dates. All three saved provider examples reconcile against the same rule. Raw checksums are unchanged after replay.

Use `Archive(path).read()` from the archive module to load a snapshot. Missing or malformed files raise errors rather than returning an empty dataset.

## Normalized Candidates

Each normalization creates a unique run directory under ignored `data/normalized/statnett/`:

- `historical_candidates.csv`: dates strictly before the original fetch date in Norway.
- `incomplete.csv`: values for the fetch date, never included in historical candidates.
- `future_quarantine.csv`: dates after the fetch date, never included in historical candidates. They are not automatically proven provider forecasts.
- `quality.json`: source archive, original UTC fetch time, slot/date/padding counts, missingness, date continuity, quarantine counts, and unresolved reference-validation status. This file is written last; a failed run may leave partial CSVs and must not be treated as a completed output.

Rows contain `observation_date`, timezone-aware `period_start_utc` and `period_end_utc`, `period_hours`, `source_index`, `production`, and `consumption`. Daily identity is the Norwegian calendar date, not a UTC date obtained by dropping the timezone. Only explicitly validated null padding is removed; genuine nulls remain missing. Unexpected source counts, non-null padding, negative/non-finite measurements, and non-midnight metadata fail normalization.

The October 2025 daily API values do not match sums of the official hourly CSV, even after simple timestamp-shift and rounding checks. No conversion factor or relabeling as MWh is applied to conceal this discrepancy. Resolve it with provider documentation or reference data before model training or database persistence.

## Project Structure

```text
src/oslo_energy/
  ingestion/
    statnett_client.py    HTTP communication only
    archive.py            JSON archival and replay
  pipeline/
    ingestion.py          Fetch-then-archive orchestration
    normalization.py      Archive replay and candidate exports
    run_ingestion.py      Command-line entry point
  transformation/
    statnett_normalizer.py Daily calendar mapping and quality metrics
  database/
    connection.py         PostgreSQL configuration for a later phase
data/examples/statnett/   Existing provider response examples
data/raw/statnett/        Runtime archives (ignored)
data/normalized/statnett/ Runtime candidates and reports (ignored)
tests/ingestion/           Mocked HTTP and archive tests
tests/pipeline/            Ingestion, replay, and CLI tests
tests/transformation/      Calendar contract and saved-response tests
docs/                     Current boundaries and future design
docker-compose.yml        Optional development PostgreSQL
```

Provider abstractions, cloud infrastructure, dashboards, and empty ML modules are deliberately deferred until there is working behavior to put in them.

## Testing

```bash
python -m pytest
python -m compileall -q src/oslo_energy
```

Tests use `httpx.MockTransport`, temporary directories, and the retained provider examples. An automatic fixture blocks real socket connections. The suite requires neither internet access nor PostgreSQL, and checks source validation, spring/autumn DST, padding, genuine missing values, current/future exclusion, archive preservation, replay provenance, and CLI failures.

## PostgreSQL and Next Steps

PostgreSQL remains the planned store for normalized historical observations. Pandas will handle normalization and later analysis; psycopg will connect the application to tables we define in PostgreSQL. JSON archives preserve source responses independently of that schema.

The old SSB implementation and monthly schema have been retired. Cleanup does not modify an existing database or delete its Docker volume. There is no active observation migration or repository in this milestone. Docker Compose and the connection helper remain available for future persistence work; development credentials must not be used in production.

The next phase should:

1. Resolve the daily API versus hourly-export discrepancy and verify measurement units/aggregation windows.
2. Verify source finality and revision/provenance rules before declaring candidates training-ready.
3. Add a PostgreSQL daily-date schema and transactional, idempotent ingestion with explicit correction handling.
4. Build analytical quality analysis, feature engineering, and time-series model evaluation later, without implicit imputation.

See [Database Design](docs/database-design.md) and [Normalization and Cleaning Design](docs/oslo_energy_data_normalization_cleaning_pipeline.md).

## License and Sources

Project source code and original documentation are covered by the [MIT License](LICENSE). That license does not apply to Statnett data, its API, or third-party material, including the archived examples. Confirm applicable provider terms before redistribution or production use.

- [Statnett operational data](https://driftsdata.statnett.no/)
- [Statnett timestamped downloads and source definitions](https://driftsdata.statnett.no/Web/Download/)
- [PostgreSQL documentation](https://www.postgresql.org/docs/)
- [Docker Compose documentation](https://docs.docker.com/compose/)