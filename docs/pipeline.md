# Data Pipeline

Use this guide to collect Statnett data, inspect normalized outputs, or replay a saved snapshot. The pipeline prepares observations for analysis; it does not yet clean data for training, engineer features, or run forecasting models.

For the project goals, start with the [README](../README.md). For measurement uncertainties, see the [Statnett investigation](statnett-source-investigation.md).

## Collect and Normalize Data

With Python 3.10 or newer, install from the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

Fetch hourly data, save a raw snapshot, and normalize it:

```bash
python -m oslo_energy.pipeline.run_ingestion --from-date 2005-01-01 --normalize
```

The command prints the archive path, observation counts, missing-value counts, and output directory. It does not contact PostgreSQL unless you add `--persist`.

Omit `--normalize` to save only the raw response:

```bash
python -m oslo_energy.pipeline.run_ingestion --from-date 2025-01-01
```

The requested start is Norwegian local midnight. A requested date range is not a guarantee that every measurement is available.

Fetch a bounded range with an inclusive local end date:

```bash
python -m oslo_energy.pipeline.run_ingestion \
  --from-date 2026-10-01 --to-date 2026-10-07 --normalize --persist
```

## Incremental Jobs

Set up PostgreSQL and apply pending migrations as described in [storage](persistence.md). Both job modes automatically archive, normalize, and persist. They end at yesterday in `Europe/Oslo`, never at an assumed fixed number of UTC hours per day.

Inspect the daily plan without fetching or writing, then run it:

```bash
python -m oslo_energy.pipeline.run_ingestion --daily --dry-run
python -m oslo_energy.pipeline.run_ingestion --daily
```

The daily mode refreshes the last three completed local dates. If committed coverage ends earlier, it starts at the date of the latest stored hour's end to catch up after outages. This is a high-water mark, not proof that earlier hours are complete; reconciliation checks those holes separately. Null measurements do not prevent the watermark advancing.

An empty database is backfilled from `2005-01-01`. Use `--start-date YYYY-MM-DD` to choose a different collection floor. Requests are split into at most 31 local days with `--chunk-days`; each chunk commits independently. HTTP, malformed-response, filesystem, and database failures return nonzero and stop the job, retaining available files for replay. Earlier committed chunks remain stored. A later daily run replans from committed coverage, not attempted requests.

Inspect or run reconciliation:

```bash
python -m oslo_energy.pipeline.run_ingestion --reconcile --dry-run
python -m oslo_energy.pipeline.run_ingestion --reconcile
python -m oslo_energy.pipeline.run_ingestion --reconcile --refresh-days 0
```

Reconciliation checks the expected UTC-hour grid from the collection floor through yesterday for absent observations or null production/consumption. It groups affected local dates and merges them with a refresh of the previous 90 days, then chunks the result. `--refresh-days 0` requests only repairs. Missing source values may remain missing after repeated requests; no zeros or interpolated values are invented.

Reconciliation recognizes the provider's exact empty-response sentinel: zero start/end/interval metadata and both measurement arrays empty. It retains the raw archive, marks the range unresolved, and continues later requests without normalizing or persisting the sentinel. Other malformed responses still stop the job. Daily and manual modes reject empty responses rather than silently skipping them.

Every non-dry reconciliation run saves a uniquely named `reconciliation_<id>.json` under the normalized output directory. It lists attempted ranges, available archive paths and fetch times, request coverage, database run IDs when stored, and unresolved/failure reasons. Its summary separates complete, unresolved, failed, and unattempted requests. Fatal failures save the summary when the filesystem permits; failures before any request is attempted only report the error. Earlier successful commits are not rolled back.

Job exit code `0` means the requested completed-hour coverage was returned with no null measurements; `1` also covers unresolved empty, truncated, or null-containing responses, even when valid rows were successfully stored. Valid partial responses do not stop later chunks. The summary describes responses received during this run, not a full post-run audit of all database history. Re-running may continue to report source gaps until the provider supplies values.

Use a daily schedule a few hours after Oslo midnight and a monthly reconciliation schedule as an initial policy. Run both from the repository root with the virtual environment's Python and the required database environment variables. The commands are scheduler-ready, but this project does not install a cron, launchd, or hosted schedule. Use scheduler-level non-overlap and failure notifications; repository transactions serialize writes, not whole jobs.

The three-day overlap and 90-day refresh are configurable policies, not verified Statnett finality guarantees. Corrections older than the refresh window require an explicit bounded refresh or a future rotating historical audit. Fetching and storage do not change training eligibility. Dry-run still needs PostgreSQL for coverage reads but makes no API requests or artifact writes.

## Inspect the Outputs

Raw snapshots are stored under `data/raw/statnett/`. Normalization creates a unique directory under `data/normalized/statnett/` for each run. Both locations are ignored by Git.

| File | What it contains |
| --- | --- |
| `historical_candidates.csv` | Hours that ended by the original fetch time, including missing measurements |
| `incomplete.csv` | Hours that had started but not ended when the data was fetched |
| `future_quarantine.csv` | Hours starting after the original fetch time |
| `quality.json` | Archive path, fetch time, interval, counts, missingness, continuity, and validation status |

**Read the quality report before using the CSVs.** It is written last; CSVs without the report can be the result of an interrupted export.

For bounded live normalization, `quality.json` also includes `request_coverage`: requested local dates, expected and returned completed UTC-hour counts, absent-hour count, and separate null counts for each metric. The CLI prints these counts. A day normally has 24 hours, but Oslo DST days have 23 or 25. Unfinished hours at the original fetch time are not counted as absent. Responses outside the requested date bounds are rejected rather than persisted.

The original quality counts describe the returned source grid, not necessarily the entire requested range. A response can have no nulls or internal gaps and still omit hours at either endpoint. Coverage reporting does not pad, interpolate, or change measurements. Unbounded requests and replay without original request bounds do not infer requested coverage; their existing quality reports remain available.

A candidate is an observation retained for further assessment, not an approved training example. `training_ready: false` remains in the report because units, source disagreement, and finality are unresolved. A missing measurement remains missing rather than becoming zero or an interpolated value.

## Replay a Snapshot

Replay normalizes an existing archive without making an API request. Supply its **original fetch time**, including a timezone; using today's time would change which periods are considered complete.

```bash
python -m oslo_energy.pipeline.run_ingestion \
  --replay path/to/archive.json \
  --fetched-at 2026-10-06T02:04:08.987326Z \
  --output-dir data/normalized/statnett
```

Replace the path and timestamp with those of your archive. Generated archive filenames contain the UTC fetch timestamp. The timestamp above is an example, not a default for other snapshots.

Replay does not rewrite the raw file. Each replay creates a new normalized output directory. Both hourly snapshots and the retained [legacy daily examples](../data/examples/statnett/) are supported; only hourly data can be persisted.

## Command Options

Run `python -m oslo_energy.pipeline.run_ingestion --help` for the CLI reference.

| Option | Behavior |
| --- | --- |
| `--from-date YYYY-MM-DD` | First requested local date; defaults to `2005-01-01`; cannot be combined with replay |
| `--to-date YYYY-MM-DD` | Inclusive local end date for manual live requests |
| `--daily` | Catch up and refresh recent completed days; automatically normalize and persist |
| `--reconcile` | Repair absent/null hours and refresh recent history; automatically normalize and persist |
| `--start-date YYYY-MM-DD` | Earliest collection date for either job mode; defaults to `2005-01-01` |
| `--overlap-days N` | Daily overlap in local dates; positive, defaults to `3` |
| `--refresh-days N` | Reconciliation refresh window; defaults to `90`; `0` repairs only |
| `--chunk-days N` | Maximum local days per job request; positive, defaults to `31` |
| `--dry-run` | Print either job's planned requests after coverage reads; do not fetch or write |
| `--archive-dir PATH` | Raw archive location; defaults to `data/raw/statnett` |
| `--normalize` | Normalize after a live fetch; replay already normalizes automatically |
| `--replay PATH` | Normalize an existing archive rather than fetch data |
| `--fetched-at TIMESTAMP` | Original timezone-aware fetch time; required for replay and invalid for live ingestion |
| `--output-dir PATH` | Normalized output location; defaults to `data/normalized/statnett` |
| `--persist` | Store completed hourly candidates; requires `--normalize` on live runs |

Relative paths are resolved from the working directory. Manual fetching, replay, daily jobs, and reconciliation are mutually exclusive source modes. Job modes always end yesterday and cannot use `--to-date`. The client caps manual future upper bounds at request time.

For database setup and replay with storage, see [PostgreSQL storage](persistence.md). Storage does not promote data to training-ready status.

## Troubleshooting

| Situation | What to check |
| --- | --- |
| Replay rejects the timestamp | Supply the original ISO timestamp with `Z` or a UTC offset |
| Live `--persist` is rejected | Add `--normalize` and complete the database setup |
| Normalization rejects a response | Inspect the retained raw archive; invalid structure is not silently repaired |
| CSVs exist but `quality.json` does not | Treat the export as incomplete; correct the filesystem problem and replay the raw archive |
| Persistence fails after normalization | The raw snapshot and normalized files remain available; correct the database problem and replay with `--persist` |
| Reconciliation returns `1` after storing rows | Read its summary JSON; unresolved provider gaps are distinct from failed requests |
| Reconciliation reports an empty response | The raw sentinel is retained; the range remains unresolved and later requests continue |
| Historical rows have blank measurements | Source values were missing; do not interpret blanks as zero |

Argument errors and failed runs return nonzero exit codes. A complete local day may still be unavailable even when some completed hours from that day have been exported.

## How the Pipeline Works

```text
Hourly API -> raw JSON archive -> normalization -> CSVs + quality report
                                                     |
                                              explicit --persist
                                                     |
                                     PostgreSQL observations + history
```

| Component | Responsibility |
| --- | --- |
| [StatnettClient](../src/oslo_energy/ingestion/statnett_client.py) | Request hourly data and decode the response |
| [Archive](../src/oslo_energy/ingestion/archive.py) | Read and write JSON snapshots without overwriting existing files |
| [StatnettIngestion](../src/oslo_energy/pipeline/ingestion.py) | Fetch, archive, and record the fetch time |
| [StatnettNormalizer](../src/oslo_energy/transformation/statnett_normalizer.py) | Validate values and map source positions to observation periods |
| [normalize_archive](../src/oslo_energy/pipeline/normalization.py) | Write the normalized files and quality report |
| [run_ingestion](../src/oslo_energy/pipeline/run_ingestion.py) | Handle command options and coordinate the workflow |
| [StatnettRepository](../src/oslo_energy/database/statnett_repository.py) | Store completed hourly observations and audit changes |

The separate [comparison tool](../src/oslo_energy/pipeline/compare_consumption.py) collects research inputs and generates the investigation chart. It is not a cleaning or training stage.

## Technical Reference

### Source Requests and Archives

The client calls `GET https://driftsdata.statnett.no/restapi/ProductionConsumption/GetData` with `Frequency=Hours`, `FromInTicks` set to the requested local midnight in UTC Unix milliseconds, and `ToInTicks` set to request time by default.

Archive names include the requested start date, UTC fetch time, and a unique identifier. They preserve decoded provider JSON, including metadata and nulls, without adding an envelope. They are not byte-for-byte HTTP captures: JSON formatting is regenerated. Invalid JSON, non-object responses, and non-finite JSON numbers fail explicitly.

Archival happens before normalization. A valid JSON response with an invalid observation structure is therefore retained for investigation.

### Hourly Validation and Columns

Required source fields are `StartPointUTC`, `EndPointUTC`, `PeriodTickMs`, `Production`, and `Consumption`.

- Hourly `PeriodTickMs` must be `3600000`.
- Endpoints must be finite, ordered Unix millisecond values aligned to UTC hours.
- Arrays must have equal lengths and exactly match the inclusive grid: `end = start + (N - 1) * 3600000`.
- Measurements must be nonnegative finite numbers or null. Booleans and numeric strings are rejected.
- No imputation, scaling, truncation, or measurement dropping is performed. Nulls become tabular `NaN` and blank CSV fields.

| Column | Meaning |
| --- | --- |
| `period_start_utc` | Timezone-aware UTC identity for the hour |
| `period_end_utc` | One elapsed hour after the start |
| `observation_date` | Local date in `Europe/Oslo`, used for grouping rather than unique identity |
| `period_hours` | `1` for hourly observations |
| `source_index` | Position in the original source arrays |
| `production`, `consumption` | Unscaled provider values; units remain unverified |

UTC identity keeps the repeated autumn local hours distinct. A Norwegian local day has 23, 24, or 25 hours. Local-day aggregates must require every expected hour and nonmissing measurements for the metric being summed; a contiguous timestamp grid can still contain missing values.

### Period Classification

| Group | Rule using the original `fetched_at` |
| --- | --- |
| Historical candidates | `period_end_utc <= fetched_at` |
| Incomplete | `period_start_utc <= fetched_at < period_end_utc` |
| Future quarantine | `period_start_utc > fetched_at` |

An ended hour is not necessarily a finalized measurement. Future quarantine describes timing, not a provider-confirmed forecast; the internal `forecast` attribute refers to this group. Completed hours from the current local day need not form a complete daily total.

### Legacy Daily Compatibility

New ingestion requests hours, but replay accepts `PeriodTickMs = 86400000` snapshots whose endpoints identify Norwegian local midnights. Daily boundaries are derived from consecutive local midnights, not fixed 24-hour UTC arithmetic.

The legacy response has one slot per local date plus a padding slot after each 25-hour autumn day. Padding is consumed only when both arrays contain null there. Genuine missing measurements remain missing; unexpected counts or non-null padding fail. These extra slots are not a forecast tail.

Daily identity is `observation_date`: dates before the original local fetch date are historical, the fetch date is incomplete, and later dates are quarantined. Daily rows are rejected by hourly persistence.

## Tests

```bash
python -m pytest
python -m compileall -q src/oslo_energy
```

The default suite blocks network access and needs no PostgreSQL service. CI runs that offline suite. See the [storage guide](persistence.md#database-tests) for opt-in database integration tests.
