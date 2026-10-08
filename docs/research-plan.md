# Research Plan

Oslo Energy aims to study **Norwegian electricity consumption forecasting** through reproducible comparisons of features, training-data quality, and forecasting methods. This document describes planned work, not implemented models or published results.

The current dataset covers national production and consumption. It does not support claims about Oslo-specific demand. Consumption is the first forecasting target; prediction horizons, model families, and additional data sources remain to be selected.

## Research Questions

1. Which input features improve forecasts, and does their usefulness change with the prediction horizon or season?
2. How do missing observations, noise, and other training-data limitations affect predictions and forecast errors?
3. Under comparable conditions, when do machine-learning models outperform statistical models and simple baselines, and when do they not?

The goal is to explain these differences, not assume that more features, cleaner-looking data, or more complex models will perform better.

## Where We Are Now

The [data pipeline](pipeline.md) collects and archives hourly observations, preserves missing values, and supports replay and optional database storage. Feature engineering, model training, and benchmark evaluation are not implemented.

We are awaiting Statnett's answers about measurement units, API/export disagreement, and revisions. The [source investigation](statnett-source-investigation.md) records the evidence. Until those questions and the training-data policy are addressed, normalized observations remain marked `training_ready: false`.

## Planned Work

| Stage | Main outcome |
| --- | --- |
| Resolve source questions | Document measurement meaning, known limitations, and a defensible data-selection policy |
| Define the forecasting task | Specify target, time resolution, forecast issue time, horizons, and evaluation periods |
| Engineer features | Build reproducible transformations that use only information available when a forecast is made |
| Establish comparisons | Implement simple baselines, suitable statistical methods, and a focused set of ML models |
| Run experiments | Measure feature and training-quality effects under consistent evaluation conditions |
| Publish findings | Release methods, reproducible configurations, performance results, and limitations |

**Feature engineering is the next development focus**, after addressing the source questions. Candidate features include past consumption, rolling summaries of past observations, calendar information, and seasonal patterns. Weather or production features require additional availability checks: future observed weather and same-hour production cannot simply be treated as known at forecast time.

These are candidate directions, not a commitment to a particular model architecture or forecast horizon.

## Experiment Design

### Feature Sets

Compare feature sets using the same dataset version, training windows, forecasting methods, and test periods. Add or remove groups of features to measure their contribution, rather than changing the data and model at the same time.

Record how each feature is calculated, how much history it needs, and when its inputs become available. Measure changes in both predictions and error, including whether improvements are consistent across horizons and seasons.

### Training-Data Quality

Keep the feature set and evaluation reference fixed while changing the training data. Define quality dimensions explicitly: missingness, noise, suspicious measurements, historical coverage, and revisions are different problems.

Distinguish naturally occurring source issues from deliberately introduced corruption. Synthetic missingness or noise can reveal sensitivity, but does not necessarily represent Statnett's actual data problems. Likewise, imputation and exclusion are experimental choices, not automatically improvements.

Evaluate every training-quality condition against the same documented, held-out reference. API/export disagreement does not establish which product is correct. If independent ground truth is unavailable, report performance against the chosen reference and disclose that limitation.

Start with separate feature and quality experiments. Study their interactions later if the initial results justify the additional scope.

### Forecasting Methods

Compare a small, purposeful set of methods before expanding the benchmark:

- **Simple baselines:** for example, using the latest available observation or a comparable previous seasonal period.
- **Statistical models:** time-series methods suited to the selected resolution and seasonal structure.
- **Machine-learning models:** methods selected for the task and available data, rather than complexity alone.

Use the same forecast horizons, test dates, information availability, and stated retraining policy. Document tuning budgets and computational cost so the comparison is interpretable. No model family has been selected yet.

## Evaluation Principles

Use chronological train/validation/test splits and evaluate forecasts at successive points in time, each using only the history available then. Reserve a final test period that is not used to select features, models, or hyperparameters.

Fit preprocessing, imputation, scaling, and feature selection only on the training portion of each evaluation split. Check that rolling features do not include their prediction target or future observations.

Revised historical measurements introduce a separate limitation: today's archive may contain information that was not available when a historical forecast would have been issued. The [database history](persistence.md) captures changes observed after collection begins; it cannot reconstruct earlier provider publication times. If historical availability cannot be established, disclose that the evaluation uses retrospectively available data rather than claiming a point-in-time operational backtest.

Report forecast error with defined metrics, not an unexplained percentage called "accuracy." MAE, RMSE, and suitable baseline-relative measures are candidates; final choices depend on the target and units. Percentage metrics require care around zero or near-zero values.

Report results by forecast horizon and season, alongside overall summaries and uncertainty estimates. Include failed approaches and cases where baselines win.

## Publication and Reproducibility

Each published experiment should identify:

- The dataset snapshot, checksums, coverage, units, exclusions, and unresolved source limitations.
- The target, horizons, temporal splits, and information available at forecast time.
- Feature definitions, preprocessing, model configurations, tuning procedure, and random seeds where relevant.
- Results for baselines, statistical models, and ML models on the same evaluation periods.
- Scripts or instructions to reproduce the experiment, with software versions and computational requirements.

We intend to publish findings and model performance, including negative results. Data and model-artifact distribution will need to respect Statnett's terms and any additional source licenses; the project's MIT license does not grant rights to third-party data.

## Contributing to the Research

Useful contributions now include source-validation evidence, missing-data analysis, tests, and clearer documentation. Proposed feature or modeling work should state the research question, required data, forecast-time availability, and evaluation method before implementation.

Return to the [project overview](../README.md) for setup and the current status.
