# Oslo Energy

A learning project building a reproducible electricity-data foundation for future consumption forecasting. Statnett is the provider; coverage is **Norway**, not Oslo or NO1 alone.

## Current Status

Implemented: hourly API fetching, raw JSON archival, DST-safe normalization, quality reports, offline replay and discrepancy research.

```text
Hourly API -> raw archive -> optional normalization -> candidate CSVs + quality report
```

Missing measurements remain missing. Completed, incomplete and future periods are separated using the original fetch time. Reports retain `training_ready: false`; units, revisions and training eligibility still need validation.

**Next:** PostgreSQL persistence with provenance and quality status. **Later:** analytical cleaning, features and models. None of these later stages is implemented yet.

## Quickstart

Use Python 3.10 or newer. From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

Fetch, archive and normalize hourly data:

```bash
python -m oslo_energy.pipeline.run_ingestion --from-date 2005-01-01 --normalize
```

The start defaults to `2005-01-01`. Omit `--normalize` for fetch-and-archive only. Runtime files go to ignored `data/raw/statnett/` and `data/normalized/statnett/`. No database or Docker service is required.

Replay an existing archive without a network request:

```bash
python -m oslo_energy.pipeline.run_ingestion \
  --replay path/to/raw.json \
  --fetched-at 2026-10-06T00:56:03.832107Z \
  --output-dir data/normalized/statnett
```

Use the original archive path and timezone-aware fetch timestamp, not replay time. The filename records its UTC fetch time. Replay supports hourly and legacy daily snapshots. Run `python -m oslo_energy.pipeline.run_ingestion --help` for all options and see the [system reference](docs/oslo_energy_data_normalization_cleaning_pipeline.md) for output contracts.

## Source Choice and Findings

We already used the API; the investigation prompted a switch from **daily API ingestion to hourly API ingestion**, not from CSV to API.

| Comparison for the same Norwegian day | Finding |
| --- | --- |
| Daily API ingestion vs summed hourly API values | Exact agreement on audited dates |
| Daily API ingestion vs summed hourly CSV exports | Consumption and production differences |

Hourly API observations are our **canonical source of truth** for future aggregates, features and training datasets. They expose missing hours and make aggregation auditable. This is an architectural choice, not proof that the API is independently correct. CSV exports remain comparison evidence, not interchangeable training inputs.

![Daily API ingestion consumption compared with hourly CSV consumption summed by Norwegian date; daily API totals are reproduced from verified hourly API sums](docs/research/statnett/consumption_discrepancy.png)

The chart compares daily ingestion with summed hourly exports on 999 complete local days. Its daily API side is reconstructed from hourly API sums and verified against the original daily archive. The [research document](docs/statnett_hourly_source_decision.md) owns the detailed evidence, coverage rules and unresolved causes, including the suspected export scaling problem.

Reproduce the research with optional plotting dependencies:

```bash
python -m pip install -e '.[dev,research]'
python -m oslo_energy.pipeline.compare_consumption
```

## Documentation

| Document | Purpose |
| --- | --- |
| [System and Pipeline](docs/oslo_energy_data_normalization_cleaning_pipeline.md) | Implemented components, contracts, outputs, replay and failure behavior |
| [Source Decision and Research](docs/statnett_hourly_source_decision.md) | Findings, visualization, source-selection rationale and open provider questions |
| [Persistence Design](docs/database-design.md) | Proposed PostgreSQL model, integrity, revisions and implementation checklist |

Runtime code lives under [src/oslo_energy](src/oslo_energy/); offline tests under [tests](tests/). Keep new system contracts in the system reference, new measurements in the research document and unimplemented storage decisions in the persistence design.

## Testing

```bash
python -m pytest
python -m compileall -q src/oslo_energy
```

Tests use mocked HTTP, temporary directories and saved examples. Real socket connections are blocked; neither internet access nor PostgreSQL is required.

## License and Sources

Project source code and original documentation are covered by the [MIT License](LICENSE). That license does not apply to Statnett data, its API, or third-party material, including the archived examples. Confirm applicable provider terms before redistribution or production use.

- [Statnett operational data](https://driftsdata.statnett.no/)
- [Statnett timestamped downloads and source definitions](https://driftsdata.statnett.no/Web/Download/)
