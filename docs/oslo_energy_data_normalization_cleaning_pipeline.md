# Oslo Energy — Data Normalization & Cleaning Pipeline

## 1. Purpose

This document describes implemented daily normalization and planned analytical stages for Statnett, the project's primary national source. Raw archival, calendar mapping, validation, candidate separation, replay, and quality reporting are implemented. PostgreSQL, analytical cleaning, features, and models remain future work. Daily API totals have not reconciled with the official hourly export, so outputs retain provider units and are explicitly not training-ready.

### Implementation Status

| Stage | Status |
| --- | --- |
| Statnett HTTP client | Implemented, with mocked request/error tests |
| Raw JSON archival | Implemented, unique snapshots with no overwrite |
| Fetch-and-archive CLI | Implemented, requested start date defaults to 2005-01-01 |
| Daily normalization and structural/value validation | Implemented; local calendar dates and verified autumn null padding |
| Completed/current/future candidate separation | Implemented using original fetch date in Norway; source finality still unverified |
| PostgreSQL observation schema and persistence | Planned |
| Quality reporting | Implemented; genuine missing values preserved, no imputation |
| Analytical cleaning | Planned |
| Feature engineering and ML | Deferred |

The implemented flow is `Statnett API -> decoded JSON -> raw archive -> optional normalization -> candidate CSVs and quality report`. Archive replay needs no API or database and requires the original timezone-aware fetch timestamp. Examples are retained under `data/examples/statnett/`; raw snapshots default to ignored `data/raw/statnett/` and unique normalized runs to ignored `data/normalized/statnett/`.

Statnett's official download definitions specify national production/consumption and local CET/CEST period starts. The daily normalizer accepts only `PeriodTickMs = 86400000`; shorter API requests may produce hourly data and must fail this daily contract. Provider units are retained as `production` and `consumption`, not relabeled as MWh while daily-versus-hourly aggregation is unresolved.

The intended long-term pipeline separates stages so that each has a single responsibility:

```text
External API
    ↓
Raw Data
    ↓
Archive
    ↓
Normalization
    ↓
Validation
    ↓
Historical / Forecast Separation
    ↓
Validated Historical Observations
    ↓
PostgreSQL
    ↓
Quality Analysis / Cleaning
    ↓
Feature Engineering
    ↓
Machine Learning
```

The primary goal is not to immediately produce a machine-learning dataset.

The goal is to establish a trustworthy data foundation from which multiple analytical and machine-learning datasets can later be produced.

---

## 2. Design Principles

The pipeline follows several principles.

### 2.1 Preserve the source

The original API response is archived before transformation.

Raw data is never modified in place.

This gives the project a reproducible source artifact that can be used to:

- investigate unexpected values
- reproduce normalization bugs
- change normalization logic later
- compare provider responses
- verify what the source actually returned

The raw archive is therefore treated as the source-level record of ingestion.

---

### 2.2 Separate interpretation from data quality

Normalization and cleaning are different operations.

Normalization determines how the provider’s representation maps to observations.

Cleaning determines whether those observations are suitable for downstream analytical use.

For example, Statnett represents a time series using:

```text
StartPointUTC
PeriodTickMs
Production[]
Consumption[]
```

Normalization expands this representation into timestamped observations.

A missing value such as:

```text
Consumption[i] = null
```

is not automatically “fixed” during normalization.

It represents information about the source and should initially remain missing.

Cleaning later determines how missing observations should be handled.

---

### 2.3 Do not destroy information unnecessarily

The pipeline should prefer preserving information and explicitly recording data-quality problems over silently modifying data.

For example:

```text
null → NaN
```

is acceptable because it represents the same missing state in the normalized representation.

However:

```text
null → interpolated value
```

is a modeling decision and should not happen implicitly during normalization.

---

### 2.4 Prevent future information leakage

The design must account for provider predictions alongside historical observations. The endpoint's prediction semantics and classification rules must be verified before historical persistence is enabled.

Future predictive values must not enter the historical observation database used for model training.

The raw response may contain those values because they are part of what the provider returned.

The implemented candidate separation distinguishes completed local dates, the incomplete fetch date, and future-date quarantine. These date-based groups are not proof of provider forecast classification or finality; training readiness remains blocked pending reference validation. A future persistence boundary must explicitly distinguish:

```text
Historical observations
Forecast observations
```

Only historical observations are eligible for persistence in the historical observation table.

This prevents future information from accidentally becoming training data.

---

## 3. Pipeline Responsibilities

### 3.1 API Client

The client is responsible only for communicating with Statnett.

Responsibilities:

- construct API requests
- send HTTP requests
- handle HTTP-level failures
- decode the response
- return the raw response structure

The client should not:

- clean values
- interpolate missing data
- create database models
- perform feature engineering
- decide how observations should be represented

Conceptually:

```text
StatnettClient
      ↓
raw Python dictionary
```

This keeps external API concerns isolated from the rest of the application.

---

## 4. Raw Data Archival

Immediately after receiving a successful response, the raw response is archived.

```text
Statnett API
      ↓
raw response
      ├──→ JSON archive
      ↓
   normalization
```

The archive preserves the decoded provider JSON without changing its values or adding a metadata envelope. It is not a byte-for-byte HTTP archive: JSON formatting is regenerated. Filenames record the requested date, UTC fetch time, and a unique identifier; repeated fetches do not overwrite earlier responses.

No transformations are performed before archival.

This means the archive may contain:

- provider metadata
- timestamps in their original representation
- production arrays
- consumption arrays
- missing values
- future forecast values
- provider-generated statistics

Even information that is not currently needed by the database should remain available in the raw archive.

### Why?

Because transformation logic is expected to evolve.

If normalization changes six months from now, we should be able to rerun it against the original response rather than relying on an already-transformed dataset.

---

## 5. Normalization

### 5.1 Objective

Normalization converts provider-specific data structures into a standard tabular representation.

For Statnett, the API represents observations positionally.

For example:

```text
StartPointUTC = T
PeriodTickMs = P

Production:
[400000, 410000, 405000]

Consumption:
[380000, 390000, 385000]
```

The raw slot index is not always a daily observation index. The simple `T + i * P` expansion is invalid for this daily series: local midnight shifts in UTC at DST, and the provider inserts a null slot immediately after each autumn 25-hour day.

The implemented normalizer converts metadata endpoints into `Europe/Oslo` local midnights and builds inclusive calendar dates. Each date consumes one measurement slot; a 25-hour day also consumes one following slot only if both arrays contain null there. It verifies the exact expected total slot count, preserves genuine missing values, and rejects unexpected/non-null padding rather than shifting dates silently.

---

### 5.2 Normalized Representation

The implemented representation is a Pandas DataFrame containing:

```text
observation_date
period_start_utc
period_end_utc
period_hours
source_index
production
consumption
```

Example:

```text
observation_date    period_hours    production    consumption
2025-10-25                  24        306011         363677
2025-10-26                  25        291889         370633
2025-10-27                  24        443551         389646
```

This representation is substantially easier to:

- validate
- sort
- inspect
- analyze
- resample
- join with other datasets
- process with Pandas
- use during feature engineering

---

## 6. Timestamp Normalization

Statnett provides timestamps as Unix milliseconds.

The normalizer converts them into timezone-aware Pandas timestamps and verifies that they identify Norwegian local midnights. Unix milliseconds are UTC instants, not assumed midnight UTC because the request used a calendar date.

The canonical daily identity is the Norwegian calendar date:

```text
observation_date
```

Timezone-aware UTC boundaries are derived from consecutive Norwegian midnights:

```text
period_start_utc
period_end_utc
```

using:

```text
Europe/Oslo
```

Timezone conversion is performed using timezone rules rather than manually applying a fixed offset.

This is important because Norway observes daylight saving time.

For example, the relationship between UTC and Norwegian local time changes during the year.

Therefore:

```text
UTC → Europe/Oslo
```

is preferred over manually adding one or two hours.

UTC remains canonical for instants and elapsed-duration comparisons; the Norwegian date remains canonical for daily identity. On DST transition days the UTC boundaries span 23 or 25 hours rather than 24. Tests verify these boundaries and local-date round trips.

---

## 7. Timestamp Integrity

The pipeline does not blindly trust the API’s timestamp metadata.

Statnett provides:

```text
StartPointUTC
EndPointUTC
PeriodTickMs
```

and the number of observations can be determined from the arrays.

The earlier fixed-period proposal was rejected by real payloads. It attempted:

```text
expected_end =
    start +
    (number_of_observations - 1) × period
```

This formula does not describe this provider's daily calendar/padding representation.

The rejected invariant was:

```text
StartPointUTC
+
(N - 1) × PeriodTickMs
=
EndPointUTC
```

The implemented invariant is `raw slots = inclusive local dates + autumn 25-hour days`, with one null padding slot in both arrays after each such day. Metadata must identify local midnight, arrays must align, and the slot count must reconcile exactly. Mismatches fail rather than trigger tail truncation or invented dates.

### Observed Counterexample

The live fetch on 2026-10-06 requested `From=2005-01-01` and returned:

| Field | Observed value |
| --- | --- |
| `StartPointUTC` | `1104534000000` = 2004-12-31 23:00 UTC |
| `EndPointUTC` | `1791237600000` = 2026-10-05 22:00 UTC |
| `PeriodTickMs` | `86400000` |
| Production array length | 7,970 |
| Consumption array length | 7,970 |
| Nulls per array | 31 |
| Inclusive-end invariant above | Does not hold |

The raw response was archived without transformation. Automated calendar mapping reconciled all 7,970 slots into 7,949 local dates plus 21 null padding slots after the autumn DST days from 2005 through 2025. Ten genuine missing measurements per array remain. Of the dates, 7,948 precede the original fetch date and one is incomplete; no future dates are created. Raw checksums remain unchanged after replay. All three saved provider examples also reconcile.

### Independent Reference Validation

The official download page constructs `GET /restapi/Download/productionconsumption/2025?fileFormat=csv`. The CSV includes `Time(Local)` with explicit offsets and production/consumption per hour. Parsed in UTC and grouped by `Europe/Oslo` date, it has 24, 25, and 24 hours for October 25, 26, and 27, 2025 respectively; March 30 has 23 hours.

However, the values do not reconcile:

| Norwegian date | Daily API consumption | Sum of hourly CSV consumption |
| --- | --- | --- |
| 2025-10-25 | 363677 | 358472.48 |
| 2025-10-26 | 370633 | 374432.00 |
| 2025-10-27 | 389646 | 386145.57 |

Simple hourly timestamp shifts (-1, +1, +2 hours) and rounding do not resolve the discrepancy. Do not infer a conversion factor or discard measurements. Investigate source revisions, aggregation windows, and provider definitions, or ask Statnett at the documented contact `trr@statnett.no`. Until resolved, reports mark `training_ready: false`, `measurement_units` as provider units, and `reference_validation` as unresolved. This normalizer verifies date mapping and raw-value preservation, not independent measurement accuracy or finality.

This protects against:

- truncated arrays
- incorrect metadata
- off-by-one errors
- incorrect period interpretation
- malformed API responses

---

## 8. Structural Validation

Before constructing the final dataset, the source structure is validated.

The pipeline verifies that required fields exist:

```text
StartPointUTC
EndPointUTC
PeriodTickMs
Production
Consumption
```

It also verifies that:

```text
len(Production) == len(Consumption)
```

because the two arrays represent measurements for the same periods.

The period must also be positive:

```text
PeriodTickMs > 0
```

Invalid source structures cause the pipeline to fail explicitly.

Silent correction is avoided.

---

## 9. Missing Data

Missing values are expected to occur in the source data.

The normalization layer preserves them.

For example:

```text
Production:
[400000, null, 420000]
```

becomes:

```text
400000
NaN
420000
```

The normalizer does not:

- interpolate
- forward-fill
- backward-fill
- replace with zero
- drop the observation automatically

The reason is that each of these choices changes the meaning of the dataset.

---

## 10. Cleaning Strategy

Cleaning is performed after normalization.

Its purpose is to identify observations that may be unsuitable for analysis or machine learning.

The first quality phase will validate and report missing values, duplicates, gaps, and invalid measurements. It will not interpolate, fill, or drop observations implicitly. Analytical cleaning will read persisted historical observations; raw snapshots remain untouched.

Cleaning will eventually address several categories of data-quality problems.

### Missing observations

Questions to measure:

- How many production values are missing?
- How many consumption values are missing?
- Which periods are affected?
- Are missing values isolated or clustered?
- Are there complete missing days?

Example metrics:

```text
production_missing_count
consumption_missing_count
production_missing_rate
consumption_missing_rate
```

### Duplicate observations

The normalized dataset should contain one observation per timestamp.

Therefore:

```text
observation_date
```

must be unique.

Duplicate timestamps indicate a structural problem and should not silently survive into the analytical dataset.

### Temporal gaps

Because this is a time-series dataset, continuity is important.

Given a daily dataset, consecutive observations should normally satisfy:

```text
observation_date[i+1] - observation_date[i] = 1 calendar day
```

Unexpected gaps should be measured explicitly.

A distinction should be made between:

```text
missing value
```

and:

```text
missing observation
```

These are not the same problem.

For example:

```text
2026-01-01    380000
2026-01-02       NaN
2026-01-03    385000
```

contains an observation with a missing value.

Whereas:

```text
2026-01-01    380000
2026-01-03    385000
```

contains a missing timestamp/observation.

Both need different handling.

---

## 11. Value Validation

Electricity measurements should be checked for physically implausible values.

Initial validation will include:

```text
production >= 0
consumption >= 0
```

Negative values are not automatically “fixed.”

They should be detected and surfaced as data-quality issues.

Future cleaning rules may also investigate:

- extreme spikes
- sudden drops
- impossible rates of change
- unusually long periods of constant values
- statistically anomalous observations

These checks should be based on the actual characteristics of the dataset rather than arbitrary thresholds introduced without evidence.

---

## 12. Historical and Forecast Separation

The implemented candidate boundary uses the original timezone-aware fetch time converted to `Europe/Oslo`: earlier dates are completed-day candidates, the fetch date is incomplete, and later dates are future quarantine. Persistence still requires independently verified observation semantics and source finality.

This creates a critical boundary in the pipeline.

The normalized dataset is divided into:

```text
historical
forecast
```

The earlier assumption of a 21-period predictive tail was contradicted by the full calendar audit: the 21 extra slots occur after historical autumn DST days, not at the end. Cross-window requests on either side of the 2024 and 2025 autumn transitions return identical overlapping daily values once the null padding is accounted for. Do not remove the last 21 records or treat all nulls as padding.

Therefore:

```text
normalized_data
      ├── historical
      └── forecast
```

The historical dataset is the only dataset passed to the historical observation repository.

The forecast dataset remains separate.

This is important because future values must not become accidental training features. Removing forecasts alone does not prove point-in-time validity: historical revisions and snapshot provenance also need consideration before model evaluation.

---

## 13. Why Forecast Data Is Dangerous

Suppose the model is being trained to predict electricity consumption.

If future API values are accidentally inserted into the historical dataset, feature engineering could produce:

```text
future consumption
        ↓
lag/rolling/statistical feature
        ↓
training data
```

The model would then indirectly receive information that would not have been available at prediction time.

This is a form of temporal data leakage.

The pipeline therefore establishes the following invariant:

> Historical training data may only contain information that was observable at the corresponding point in time.

Separating forecast observations before persistence makes this failure mode much harder to introduce accidentally.

---

## 14. Database Boundary

The database stores normalized historical observations.

The database is not responsible for performing machine-learning feature engineering.

The intended flow is:

```text
Normalized historical observations
            ↓
        PostgreSQL
            ↓
        Pandas read
            ↓
         Cleaning
            ↓
     Feature engineering
            ↓
       Model training
```

The observation database should represent the underlying electricity measurements rather than a particular model’s feature representation.

This allows multiple models and feature-engineering strategies to operate against the same underlying data.

---

## 15. Database Data Model

The proposed future observation model is conceptually:

```text
statnett_electricity_observations
observation_date
consumption
production
ingested_at
```

The observation period should be uniquely constrained.

Conceptually:

```text
UNIQUE(observation_date)  -- for the verified national daily series
```

This protects against duplicate ingestion.

The ingestion timestamp represents when Oslo Energy received the observation and is separate from the observation’s actual timestamp.

These represent two different concepts:

```text
period
    When electricity was measured.

ingested_at
    When Oslo Energy received the data.
```

---

## 16. Idempotent Ingestion

The ingestion pipeline should be safe to run repeatedly.

If the same Statnett response is processed twice, it should not create duplicate observations.

Conceptually:

```text
API
 ↓
normalize
 ↓
repository
 ↓
UPSERT / conflict handling
```

This makes the ingestion process resilient to:

- retries
- scheduled executions
- Lambda retries
- network failures after successful database writes
- repeated historical backfills

Idempotency becomes particularly important when the pipeline eventually moves from local execution to AWS.

---

## 17. Quality Measures

The pipeline will measure data quality rather than assuming the data is clean.

The initial quality checks are:

| Category | Measure |
|---|---|
| Structure | Required fields present |
| Structure | Production/consumption array lengths match |
| Time | Start/end timestamps consistent |
| Time | Timestamps timezone-aware |
| Time | Timestamps ordered |
| Time | Timestamps unique |
| Time | Expected temporal frequency |
| Values | Production non-negative |
| Values | Consumption non-negative |
| Missingness | Production missing count/rate |
| Missingness | Consumption missing count/rate |
| Completeness | Missing timestamps/gaps |
| Leakage | Forecast data excluded from historical persistence |
| Persistence | Duplicate observations prevented |
| Reproducibility | Raw response archived |

These checks form the initial data-quality contract.

---

## 18. Failure Philosophy

The pipeline should fail loudly when it encounters a structural violation.

For example:

```text
Production and Consumption lengths differ
```

should produce an error.

It should not silently truncate the longer array.

Likewise:

```text
Expected end timestamp != API end timestamp
```

should fail normalization.

The general rule is:

> If the pipeline cannot confidently determine what an observation means, it should not silently invent an interpretation.

This is preferable to producing a seemingly valid but corrupted dataset.

---

## 19. Normalization vs Cleaning vs Feature Engineering

These stages have deliberately different responsibilities.

### Normalization

Answers:

> “How do I turn this provider’s representation into observations?”

Examples:

- expand arrays
- construct timestamps
- convert Unix milliseconds
- apply timezone information
- standardize column names
- separate historical and forecast horizons

### Cleaning

Answers:

> “Are these observations reliable and suitable for analysis?”

Examples:

- identify missing values
- identify temporal gaps
- detect duplicates
- detect invalid values
- quantify anomalies
- determine appropriate missing-data treatment

### Feature Engineering

Answers:

> “What information should the model use to make predictions?”

Examples that will be investigated later:

- calendar features
- lag features
- rolling statistics
- trend features
- production/consumption relationships

Feature engineering should not be mixed into normalization or cleaning.

---

## 20. Current Milestone and Next Implementation

The default command still fetches and archives only. Opt-in `--normalize` and `--replay` extend it to:

```text
Raw JSON Archive -> StatnettNormalizer -> candidate CSVs + quality.json
```

Replay requires the original fetch timestamp and performs no API request. Normalization validates fields, numeric values, local-midnight metadata, daily frequency, array alignment, and date/padding counts. Each run gets a unique output directory with completed-day candidates, incomplete values, future quarantine, and a report linking back to the raw source. Raw snapshot uniqueness and normalized run uniqueness are different from future database idempotency.

Implemented normalized data includes `observations`, `historical` (completed-day candidates), `incomplete`, `forecast` (future-date quarantine, not proven predictions), and `quality`. Source units and finality remain explicitly unverified. The next implementation target, after resolving reference validation, is:

```text
StatnettClient
      ↓
raw response
      ↓
Raw JSON Archive
      ↓
StatnettNormalizer
      ├── validate source structure
    ├── map Norwegian calendar dates
    ├── derive UTC boundaries with DST rules
      ├── construct DataFrame
      ├── validate timestamp range
      ├── validate values
    └── split completed / incomplete / future candidates
      ↓
NormalizedStatnettData
      ├── historical
    ├── incomplete
      └── forecast
      ↓
PostgreSQL repository
```

Cleaning and analytical quality analysis will operate on the normalized historical observations rather than modifying the raw source.

---

## 21. Testing Strategy

Current tests cover the HTTP client, archival, normalizer, replay, quality reporting, and CLI without live API or database connections. Existing examples are replayed to verify all slot/date counts. Calendar tests cover spring/autumn transitions, missing values, unexpected padding, invalid source structures, UTC/local round trips, incomplete/future exclusion, and original fetch-time provenance. Independent hourly-value reconciliation remains unresolved; database tests are future work.

The normalizer is tested without depending on the live Statnett API.

Tests reuse the existing archived provider examples and small fixed transition responses rather than adding duplicate large datasets.

Tests should verify:

1. Unix milliseconds become the expected UTC timestamp.
2. Local timestamps correctly use `Europe/Oslo`.
3. Production and consumption arrays become aligned observations.
4. Missing source values remain missing.
5. Array length mismatches are rejected.
6. Invalid periods are rejected.
7. Start/end timestamp inconsistencies are rejected.
8. Duplicate timestamps are rejected.
9. Negative measurements are rejected.
10. The forecast horizon is separated from historical observations.
11. Historical observations do not contain forecast records.
12. Repeated persistence does not create duplicates.

This gives the transformation layer a deterministic contract.

---

## 22. Long-Term Pipeline

The eventual Oslo Energy architecture is intended to evolve toward:

```text
                 ┌──────────────────┐
                 │   External APIs   │
                 └────────┬─────────┘
                          ↓
                 ┌──────────────────┐
                 │      Clients     │
                 └────────┬─────────┘
                          ↓
                 ┌──────────────────┐
                 │   Raw Archives   │
                 └────────┬─────────┘
                          ↓
                 ┌──────────────────┐
                 │  Normalization   │
                 └────────┬─────────┘
                          ↓
                 ┌──────────────────┐
                 │ Validate / Split │
                 └────────┬─────────┘
                          ↓
                 ┌──────────────────┐
                 │    PostgreSQL    │
                 └────────┬─────────┘
                          ↓
                    Quality / Cleaning
                          ↓
                      Pandas / NumPy
                          ↓
                 ┌──────────────────┐
                 │ Feature Families │
                 └────────┬─────────┘
                          ↓
              ┌───────────┴───────────┐
              ↓                       ↓
        Baseline Models        Statistical Models
              │                       │
              └───────────┬───────────┘
                          ↓
                       XGBoost
                          ↓
                  Time-Series Evaluation
```

The architecture deliberately keeps the underlying observations independent from the eventual machine-learning strategy.

The purpose of this phase is therefore not simply to “clean a dataset.”

It establishes a trust boundary between external data and the analytical system.

External data is considered untrusted until its structure and meaning have been validated.

Once normalized and validated, historical observations become the project’s persistent analytical foundation.
