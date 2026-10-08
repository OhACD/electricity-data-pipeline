# Oslo Energy

An open-source research project studying electricity consumption forecasting in Norway. We aim to understand how input features and training-data quality affect predictions, and how machine-learning models compare with statistical forecasting methods and simple baselines.

The project currently provides the data pipeline for that research. It collects Statnett's public production and consumption data, preserves source snapshots, and prepares hourly observations for analysis. Despite the name, the active dataset covers **Norway as a whole**, not Oslo or the NO1 price area.

## Research Goals

- Measure how different feature sets change forecasts and prediction errors.
- Study the effect of missing, noisy, or otherwise lower-quality training data.
- Compare machine-learning models with statistical models and simple forecasting baselines under the same evaluation conditions.
- Publish reproducible experiments, findings, and model performance, including results where simpler methods perform better.

**Consumption forecasting is the first planned task.** Forecast horizons and model choices are not yet fixed. The [research plan](docs/research-plan.md) describes the proposed experiments and evaluation approach. No forecasting results have been published yet.

## Current Status

The data pipeline supports hourly API collection, immutable raw JSON archives, DST-safe normalization, quality reports, offline replay, optional PostgreSQL storage with correction history, and incremental daily/reconciliation job modes.

```text
Statnett hourly API -> raw archive -> normalized CSVs + quality report
                                              -> optional PostgreSQL storage
```

We are **awaiting answers from Statnett** about differences between its API and exported data, measurement units, and how observations are revised. These questions matter for interpreting values and evaluating historical forecasts. See the [source investigation](docs/statnett-source-investigation.md) for the evidence.

Normalized data retains missing values and is marked `training_ready: false`: consistent structure and successful storage do not establish measurement accuracy or suitability for training. Analytical cleaning, feature engineering, and models are not implemented.

**Next:** address the source questions, define the forecasting task, and move into feature engineering. We will then build baselines, statistical models, and machine-learning models, evaluate them, and publish the findings.

## Quickstart

Use **Python 3.10 or newer**. From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

Fetch, archive and normalize hourly data:

```bash
python -m oslo_energy.pipeline.run_ingestion --from-date 2005-01-01 --normalize
```

This command contacts Statnett, saves a JSON snapshot under `data/raw/statnett/`, and writes a new run directory under `data/normalized/statnett/`. The normalized outputs are:

- `historical_candidates.csv`: hours that ended by the original fetch time, including missing measurements.
- `incomplete.csv`: hours that had started but not ended.
- `future_quarantine.csv`: hours starting after the original fetch time.
- `quality.json`: coverage, missing-value counts, and validation status.

The command prints the archive and output locations. The start date defaults to `2005-01-01`; omit `--normalize` to fetch and archive only. Generated data is ignored by Git. **Docker and PostgreSQL are not required for this workflow.**

See the [pipeline guide](docs/pipeline.md) for replay, options, and troubleshooting, or the [PostgreSQL guide](docs/persistence.md) for optional storage.

With PostgreSQL configured and migrations applied, preview or run incremental collection:

```bash
python -m oslo_energy.pipeline.run_ingestion --daily --dry-run
python -m oslo_energy.pipeline.run_ingestion --daily
python -m oslo_energy.pipeline.run_ingestion --reconcile
```

Daily mode catches up and refreshes three completed Oslo dates. Reconciliation repairs gaps/nulls and refreshes 90 days. Both automatically archive, normalize, and persist in bounded chunks. An empty database backfills from 2005 unless `--start-date` is supplied. See [incremental jobs](docs/pipeline.md#incremental-jobs) for policies and scheduling; no schedule is installed automatically.

## Documentation

| Guide | Read it to... |
| --- | --- |
| [Research Plan](docs/research-plan.md) | Understand the questions, planned experiments, and publication goals |
| [Pipeline](docs/pipeline.md) | Collect data, inspect outputs, replay snapshots, and understand normalization |
| [PostgreSQL Storage](docs/persistence.md) | Configure optional storage and understand correction history |
| [Statnett Investigation](docs/statnett-source-investigation.md) | Review the source discrepancies, evidence, and open questions |

Implementation lives under [src/oslo_energy](src/oslo_energy/), with tests under [tests](tests/).

## Development and Contributions

```bash
python -m pytest
python -m compileall -q src/oslo_energy
```

The default test suite runs without API access or PostgreSQL. Database integration tests are opt-in; see the [PostgreSQL guide](docs/persistence.md).

Contributions are welcome, especially source-validation evidence, reproducible data-quality analyses, tests, and documentation improvements. For features or models, discuss the forecasting task and evaluation design in an issue before building a new experiment. See the [research plan](docs/research-plan.md) for the current direction.

## License and Sources

Project source code and original documentation are covered by the [MIT License](LICENSE). That license does not apply to Statnett data, its API, or third-party material, including the archived examples. Confirm applicable provider terms before redistribution or production use.

- [Statnett operational data](https://driftsdata.statnett.no/)
- [Statnett timestamped downloads and source definitions](https://driftsdata.statnett.no/Web/Download/)
