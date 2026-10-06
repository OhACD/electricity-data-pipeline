# System and Pipeline

This is the reference for implemented behavior. Start with the [README](../README.md) for commands, the [source research](statnett_hourly_source_decision.md) for findings and the [persistence design](database-design.md) for the next stage.

## Current System

```text
Hourly Statnett API -> raw JSON archive -> optional normalization
                                              |
                                              v
                          completed / incomplete / future candidates
                                              |
                                              v
                                   CSV files + quality report
```

Fetch-and-archive is the CLI default. `--normalize` adds normalization after archival; `--replay` normalizes an existing snapshot without a network request. PostgreSQL writes, analytical cleaning, features and models are not implemented.

Hourly API observations are the canonical measurement layer for future datasets and model training. Raw snapshots retain evidence; derived datasets must retain their lineage. This source choice does not independently establish accuracy or training readiness.

## Responsibilities

| Component | Responsibility |
| --- | --- |
| [StatnettClient](../src/oslo_energy/ingestion/statnett_client.py) | Request hourly data, handle HTTP failures and decode a JSON object |
| [Archive](../src/oslo_energy/ingestion/archive.py) | Read/write JSON snapshots without overwriting existing files |
| [StatnettIngestion](../src/oslo_energy/pipeline/ingestion.py) | Fetch, archive, record the aware fetch time and return the archive path |
| [StatnettNormalizer](../src/oslo_energy/transformation/statnett_normalizer.py) | Validate structure/values, map periods and split candidates |
| [normalize_archive](../src/oslo_energy/pipeline/normalization.py) | Replay a snapshot and write candidate files plus a quality report |
| [run_ingestion](../src/oslo_energy/pipeline/run_ingestion.py) | Parse CLI options, orchestrate the run and return a nonzero status on failure |
| [compare_consumption](../src/oslo_energy/pipeline/compare_consumption.py) | Archive research inputs, compare complete local days and generate the chart |

Clients do not clean data or write database rows. Normalization does not impute values or construct model features.

## Source and Archive Contract

The client calls `GET https://driftsdata.statnett.no/restapi/ProductionConsumption/GetData` with:

| Parameter | Meaning |
| --- | --- |
| `FromInTicks` | Requested Norwegian local midnight converted to UTC Unix milliseconds |
| `ToInTicks` | Request time by default |
| `Frequency` | `Hours` |

The CLI start defaults to `2005-01-01`. It has no end-date flag; the client supports inclusive `to_date` bounds for research and caps future upper bounds at request time. Requested dates do not guarantee complete returned coverage.

Raw responses go to ignored `data/raw/statnett/`. Filenames record requested start, UTC fetch time and a unique identifier. Archives preserve the decoded provider JSON, including metadata and nulls, with no extra envelope. They are not byte-for-byte HTTP captures: JSON formatting is regenerated. Invalid JSON, a non-object response or non-finite JSON numbers cause failure.

Archival precedes normalization, so a valid JSON response with an invalid observation structure remains available for investigation. Existing raw files are never rewritten by replay. Missing or malformed archives fail explicitly.

## Hourly Normalization Contract

Required fields are `StartPointUTC`, `EndPointUTC`, `PeriodTickMs`, `Production` and `Consumption`.

- `PeriodTickMs` must be `3600000` for hourly data.
- Endpoints must be finite Unix milliseconds, ordered and aligned to UTC hour boundaries.
- Both arrays must have equal lengths and match the inclusive UTC grid exactly: `end = start + (N - 1) * 3600000`.
- Measurements must be nonnegative finite numbers or null; booleans and numeric strings are rejected.
- Source nulls become tabular `NaN`, not zeros. No interpolation, scaling, truncation or measurement dropping occurs.

The output columns are:

| Column | Meaning |
| --- | --- |
| `period_start_utc` | Timezone-aware UTC identity |
| `period_end_utc` | One elapsed hour after the start |
| `observation_date` | Norwegian date derived using `Europe/Oslo`; grouping metadata, not a unique key |
| `period_hours` | `1` for hourly observations |
| `source_index` | Position in the original arrays |
| `production`, `consumption` | Unscaled provider values; units not independently verified |

UTC identity preserves both occurrences of the repeated autumn local hour. A Norwegian day contains 23, 24 or 25 hourly periods, each lasting one elapsed hour. Do not group by a timezone-stripped UTC date or assume every local day has 24 hours.

## Candidate Eligibility

Replay requires the **original timezone-aware fetch timestamp**, not replay time. The hourly split is:

| Output group | Rule |
| --- | --- |
| Historical candidates | `period_end_utc <= fetched_at` |
| Incomplete | `period_start_utc <= fetched_at < period_end_utc` |
| Future quarantine | `period_start_utc > fetched_at` |

Completed hours of the current day can be candidates without forming a complete daily total. A completed period is not proof of finality, and future quarantine is not proof that the provider labeled a value as a forecast. The internal `forecast` attribute means future quarantine, not certified predictions.

Future daily aggregates must require every expected hour and nonmissing measurements for the metric being summed. A normalizer missing-value count and a missing timestamp are different issues; a contiguous grid can still contain many null measurements.

## Output and Failure Contract

Every normalization creates a unique run directory under ignored `data/normalized/statnett/`:

| File | Contents |
| --- | --- |
| `historical_candidates.csv` | Completed-period candidates, including preserved missing values |
| `incomplete.csv` | Started but unfinished periods |
| `future_quarantine.csv` | Periods starting after the original fetch time |
| `quality.json` | Source archive, original UTC fetch time, frequency/interval, counts, missing rates, continuity and validation status |

The report distinguishes raw slots, local dates, observations and legacy padding. It records `training_ready: false` and unresolved units/reference validation. It is written last: a failed export can leave partial CSVs, and a run without its quality report is incomplete. Directory uniqueness is not database idempotency.

Structure violations fail rather than silently inventing an interpretation. Validating these contracts establishes reproducible mapping, not independent measurement accuracy.

## Legacy Daily Replay

Saved [daily examples](../data/examples/statnett/) remain supported; new ingestion does not request the legacy daily response.

For `PeriodTickMs = 86400000`, endpoints must identify Norwegian local midnights. The normalizer builds inclusive local dates and independently localized next-midnight boundaries, giving 23/24/25-hour daily periods. Fixed 24-hour arithmetic in UTC is not valid for this representation.

The legacy slot contract is `raw slots = local dates + autumn 25-hour days`. Each autumn day consumes one following padding slot only if **both** arrays contain null there. Genuine null measurements remain missing; unexpected counts or non-null padding fail. The extra slots are not a forecast tail.

Legacy daily identity is `observation_date`. Earlier dates are historical candidates, the original fetch date is incomplete and later dates are quarantined. Daily replay rows must not be treated as hourly observations in future persistence.

## Quality and Future Use

Current quality reporting covers missingness, counts, UTC/date mapping, continuity and candidate eligibility. Research demonstrates API/export disagreement; detailed evidence and the visualization belong in the [research document](statnett_hourly_source_decision.md), not the operational contract.

The next milestone is provenance-preserving PostgreSQL storage, as proposed in the [persistence design](database-design.md). Storing observations is distinct from approving them for model training. Before training, verify units, missing-data treatment, revisions and what would have been available at prediction time. Later cleaning and features must not overwrite raw or canonical source measurements.

## Verification

```bash
python -m pytest
python -m compileall -q src/oslo_energy
```

Tests block real socket connections and need no database. They cover HTTP failures, archival, hourly and legacy daily contracts, DST, missing values, fetch-time cutoffs, offline replay, CLI failures and complete-day research comparisons. Live checks and snapshot-specific counts are recorded separately in the research document.
