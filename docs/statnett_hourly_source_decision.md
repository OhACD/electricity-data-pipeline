# Statnett Hourly Source Decision and Discrepancy Research

Research and source decision: 2026-10-06.

## The Comparison That Exposed the Discrepancy

**We first found the discrepancy by comparing daily API ingestion values against hourly CSV export values summed into daily totals.** Each comparison uses the same Norwegian calendar date and all its expected hours; it does not compare a daily total with a single hourly measurement.

There are two different hourly products, which must not be conflated:

| Comparison | Result |
| --- | --- |
| Daily API ingestion vs summed hourly API values | Exact agreement on audited dates |
| Daily API ingestion vs summed hourly CSV export values | Discrepancy |
| Hourly API vs hourly CSV at matched UTC timestamps | Discrepancy already present before daily aggregation |

This progression explains both the original daily-versus-hourly finding and its localization to the API/export data paths. Switching ingestion to the hourly API improves inspection and completeness checks, but does not automatically remove the discrepancy.

## Decision

New ingestion uses Statnett's **hourly API**, not the annual CSV/Excel export or the legacy `From`-only daily response:

```text
GET https://driftsdata.statnett.no/restapi/ProductionConsumption/GetData
    ?FromInTicks=<UTC Unix milliseconds>
    &ToInTicks=<UTC Unix milliseconds>
    &Frequency=Hours
```

The requested start is Norwegian local midnight converted to UTC. The default end is request time. Normalization requires `PeriodTickMs = 3600000`, inclusive UTC hour endpoints and exactly one array slot per hour. The client also supports an inclusive `to_date` for bounded research requests; future upper bounds are capped at request time.

The provider and national scope have not changed. This is a resolution/request-contract change, **not a claim that hourly data fixes the export discrepancy**. Existing daily archives remain replayable and unchanged.

### Canonical Data Foundation

**The discrepancy investigation prompted our decision to make hourly API observations the project's main source of truth and scaffolding for future datasets and model training.** Hourly granularity lets us inspect the contributions behind daily values, retain missingness and apply explicit completeness checks before constructing analytical datasets.

The intended lineage is:

```text
Archived hourly API response
    -> normalized UTC-hour observations
    -> validated analytical data and optional daily aggregates
    -> features and training targets
    -> model training and evaluation
```

Raw snapshots retain provider evidence; normalized hourly observations form the canonical measurement layer. Future daily totals, features and training targets must derive from this layer with documented transformations and provenance. Legacy daily responses and hourly CSV exports remain research/replay references; they must not be silently substituted for or mixed into the canonical training inputs.

Here, **source of truth is an architectural choice, not proof of measurement accuracy**. Units, eligibility, missingness handling, historical revisions and point-in-time availability still require validation before training. Choosing this foundation does not enable training or remove `training_ready: false`.

## Why Hourly Ingestion

1. **Auditable aggregation.** Inspect every contribution to a daily total and require every expected hour. A daily value can conceal a partially missing day.
2. **Unambiguous identity.** `period_start_utc` uniquely identifies an hour, including the repeated autumn local hour. Daily grouping uses `Europe/Oslo`, with 23, 24 or 25 expected hours.
3. **Simpler calendar handling.** New hourly records need no legacy daily autumn null-padding correction. Genuine nulls are retained.
4. **Better quality analysis.** Distinguish missing hours, unfinished periods and future periods. Later analysis can use hourly information or explicitly derived daily values.
5. **Avoid the known export anomaly.** Exported production contains a temporary roughly fourfold scaling difference. Retain the API as ingestion source; exports remain a comparison dataset.

Units, finality and point-in-time availability remain unverified. Reports retain `training_ready: false`. No imputation, blanket scaling, database writes or ML features are introduced.

## Research Findings

### Internal API Agreement

The earlier 2024-2026 audit compared 1,001 dates and found exact agreement between daily API values and summed hourly API values for both metrics. Internal consistency is not independent accuracy validation: the endpoints may share a source or calculation.

That audit included a partially missing day. November 17, 2025 has only 22 valid consumption hours; its daily API value can match those 22 hours without representing a complete day.

### Legacy Daily Calendar Audit

The original `From=2005-01-01` daily snapshot fetched on 2026-10-06 contained 7,970 slots per array spanning 7,949 Norwegian dates. Of 31 nulls per metric, 21 were padding immediately after autumn DST days from 2005 through 2025; the remaining 10 were genuine missing measurements. They were not 21 forecast days at the end of the response.

Replay reconciled those slots into 7,948 earlier-date candidates and one incomplete fetch date, with no invented future dates. All three retained daily examples satisfy the same rule, and raw checksums were unchanged after replay. The [system reference](oslo_energy_data_normalization_cleaning_pipeline.md) describes the retained daily replay contract; these counts belong to that particular snapshot.

### Export Disagreement

Differences exist at matched UTC hourly timestamps, before our daily normalization. CSV and Excel contain identical timestamps and values across all 8,761 rows of the 2025 export, ruling out a CSV-only parsing issue. Simple hour shifts and rounding do not reconcile the datasets. Differences outside DST dates and within matched hours rule out DST as the sole cause.

### Production Scaling Interval

Export production is approximately one quarter of API production for **552 consecutive hours**, from **2025-03-18 00:00 UTC inclusive** to **2025-04-10 00:00 UTC exclusive**. Those boundaries are 01:00 CET and 02:00 CEST respectively.

The median API/export ratio is **4.02675**. Multiplying exported production by four inside this interval reduces the median absolute difference to **0.9812%**, versus **0.9843%** outside it. At the first affected hour, production is 20,883 versus 5,175.69, while consumption still agrees within rounding: 16,867 versus 16,867.48.

This supports a temporary scaling/unit/resolution anomaly in the export data path. A quarter-hour-to-hour conversion problem is plausible, but **not provider-confirmed**. No correction is applied and the export is not assumed authoritative.

## Consumption Visualization

![Daily API ingestion consumption compared with summed hourly CSV consumption in 2024, 2025 and 2026; hourly API sums reproduce daily ingestion totals](research/statnett/consumption_discrepancy.png)

The chart makes the original comparison explicit: **daily API ingestion consumption versus hourly CSV consumption summed over the same local day**. It measures disagreement, not error against known ground truth:

$$
\text{difference (\%)} = 100 \times
\frac{\text{daily API ingestion consumption} - \sum \text{hourly CSV consumption}}
     {\sum \text{hourly CSV consumption}}
$$

The chart generator uses summed hourly API values to reconstruct the daily API side. All **999 plotted consumption totals were verified to equal the original daily-ingestion archive exactly**. Therefore the plotted values represent the same daily-versus-hourly-export comparison; they are not evidence of disagreement between daily API and hourly API resolutions. The saved table retains `api_consumption` as the hourly-derived daily total.

Positive means daily ingestion is larger than the summed hourly export; negative means it is smaller. The CSV denominator is a comparison convention, not an endorsement of correctness. Zero denominators have no percentage result. This equality was verified for the documented snapshots, not guaranteed for arbitrary future re-fetches.

### Coverage Rules

Derive expected hours from consecutive Norwegian local midnights. Include a date only when both sources have exactly the expected 23/24/25 unique UTC hours, every paired consumption value is finite and nonmissing, and every API period ended by the original fetch time.

Incomplete days have no totals or error percentages in the saved table; gaps break the plotted line. Nulls are never replaced with zero or summed into a seemingly complete day.

### Complete-Day Results

Fresh snapshots were fetched on 2026-10-06. The 2026 comparable range ends on October 5.

| Year | Comparable dates | Mean signed difference | Median absolute difference | P90 absolute difference | Maximum absolute difference |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2024 | 365 | +2.35718% | 2.43061% | 3.73079% | 5.13771% |
| 2025 | 356 | +0.21722% | 0.00096% | 1.88790% | 4.72203% |
| 2026 | 278 | +1.11989% | 1.21000% | 2.01543% | 3.23205% |

Total: **999 complete comparable dates**. Exclusions: November 21, 2024 lacks one CSV hour; November 17-25, 2025 have missing API measurements (two hours on November 17, all hours on November 18-25); October 6, 2026 is partial.

**Correction to preliminary results:** the earlier audit reported 357 comparable 2025 dates and a maximum consumption difference of 6.97125%. It compared November 17's 22-hour API sum with a complete CSV day. Excluding that invalid comparison gives 356 dates and a maximum of 4.72203%.

### Consumption Patterns

- January-June 2025: 4,260 of 4,343 paired hours agree within 0.5 provider units; median absolute hourly difference is 0.00158%.
- July 9, 2025: differences exceed rounding from **06:00 CEST (04:00 UTC)**, becoming substantially larger from 08:00 CEST. They did not begin on July 1.
- July-December 2025: median absolute hourly difference is **1.32232%**.
- The near-zero 2025 daily median hides second-half disagreement; the chart exposes the change.
- Consumption and production differences partly track each other afterward, but their hourly difference correlation is only about 0.590 after July 9 and 0.638 in 2026. A shared production/net-exchange change does not explain every residual.

## Unconfirmed Causes and Validation Gates

### Does the Export Endpoint Have a Server-Side Problem?

The evidence strongly suggests a problem or inconsistent processing in Statnett's **export data path**, especially the temporary roughly fourfold production scaling difference. The same values appear in CSV and Excel, and the mismatch exists before our normalization. This rules out our CSV parsing as the explanation, but does not identify whether the cause is stored source data, unit conversion, aggregation, revisions or another backend process. Statnett has not confirmed a specific server bug.

We **already used the API before this investigation**: the previous ingestion requested daily API data, not CSV. We switched from daily to hourly API ingestion for finer-grained traceability and completeness checks, and retained exports as comparison evidence rather than adopting them as the canonical source. We did not demonstrate that hourly API values correct the daily API; they agree on audited dates.

Public endpoints expose differences, not backend calculations or revisions. Published descriptions distinguish real-time estimates and ENTSO-E-sourced overview data, but do not establish which source or revision policy explains this particular mismatch. Do not present those descriptions as a confirmed cause.

Ask the documented provider contact, `trr@statnett.no`, to clarify:

1. Why production changes scale during March 18-April 10, 2025, and whether quarter-hour energy/resolution conversion is involved.
2. What changed in consumption processing around July 9, 2025, 04:00-06:00 UTC.
3. Units, aggregation windows, revision policy and the intended relationship between hourly `GetData` and annual exports.

No independent ground truth has been established. Hourly ingestion improves traceability and completeness checks; it does not close these questions or authorize training. The [persistence design](database-design.md) separates provenance-preserving candidate storage from training approval; storing a value must not certify its accuracy.

### Bounded Storage Validation Gate

Executed against the retained archives on October 6, 2026, the bounded gate
confirmed 190,753 completed hourly slots with unique, contiguous UTC identities.
Three annual requests overlap that snapshot on 8,784 (2024), 8,760 (2025), and
6,673 (2026) hours; production, consumption and nulls match exactly in each
overlap. The spring and autumn DST checks yielded 23 and 25 hours respectively;
the March 18/April 10 production boundaries and July 9 consumption transition
each retained complete 24-hour local dates. November 17, 2025 has two nulls per
series, while November 18 has 24; these remain missing, not imputed. The strict
daily comparison remains 999 complete dates. Focused normalization and comparison
tests passed (59 tests). This confirms internal structural consistency only, not
independent accuracy or finality.

The published translations describe export values as "MW per hour" and Norway
overview production/consumption as ENTSO-E sourced. Neither statement resolves
the precise `GetData` units, revision policy or export discrepancy. ENTSO-E is
therefore not an independent benchmark without further lineage evidence; no
independent comparison tolerance or prediction target has been agreed.

Provider follow-up is pending: the questions above are a draft, not a sent email
or a written reply. Also ask whether values are hourly mean power or integrated
energy, which sources feed each endpoint, when estimates become final, and which
publication/revision timestamps are available. Do not wait for a reply to store
structurally valid candidates, but keep `training_ready=false`.

Repeated ingestion of a fixed historical range with opt-in persistence can
capture future system vintages using immutable archives, fetch times and
change-only database audits. This is a manual workflow, not an installed schedule
or a reconstruction of past publication times. Database recording time and
system fetch time remain distinct; neither is an asserted provider publication
time.

## Reproduction

From the repository root:

```bash
python -m pip install -e '.[dev,research]'
python -m oslo_energy.pipeline.compare_consumption
```

The command archives decoded API JSON and original CSV bytes under ignored `data/raw/statnett/comparison/`. It writes the PNG, [daily comparison table](research/statnett/consumption_discrepancy.csv), and timestamped provenance JSON under `docs/research/statnett/`. Provenance records URLs, requested dates, separate fetch times, archive paths, SHA-256 checksums, coverage rules and statistics. Matplotlib is optional; ingestion does not need it.

The saved artifacts capture this run, with its [provenance record](research/statnett/provenance_20261006T020353656523Z.json). Re-fetching later can change data and coverage. Retain original raw snapshots for exact replay; the comparison table records exclusions as well as plotted dates.

Run hourly ingestion independently:

```bash
python -m oslo_energy.pipeline.run_ingestion --from-date 2005-01-01 --normalize
```

A live 2026-10-06 smoke test normalized 190,753 hourly slots across 7,949 local dates, with no padding and 293 missing measurements per series. Counts are snapshot-specific, not proof of complete coverage.

## Sources

- [Official chart request implementation](https://driftsdata.statnett.no/web/Scripts/ProductionConsumption/ProductionConsumptionDataService.js)
- [Statnett download definitions](https://www.statnett.no/for-aktorer-i-kraftbransjen/tall-og-data-fra-kraftsystemet/last-ned-grunndata/)
- [2025 CSV endpoint](https://driftsdata.statnett.no/restapi/Download/productionconsumption/2025?fileFormat=csv)
- [Published source descriptions](https://driftsdata.statnett.no/restapi/Translator/Translations?language=en)

The MIT license does not grant rights to Statnett's underlying data. Confirm provider terms before redistribution or production use.
