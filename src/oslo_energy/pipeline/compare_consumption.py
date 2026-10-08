"""Reproduce the hourly API versus export consumption comparison and chart."""

import argparse
from datetime import datetime, timezone
import hashlib
import io
from pathlib import Path

import httpx
import pandas as pd

from oslo_energy.ingestion.archive import Archive
from oslo_energy.ingestion.statnett_client import StatnettClient
from oslo_energy.transformation.statnett_normalizer import StatnettNormalizer


def compare_consumption(
    observations: pd.DataFrame, exported: pd.DataFrame, *, fetched_at: datetime
) -> pd.DataFrame:
    """Compare hourly API and export consumption on complete Oslo dates.

    Both frames require unique, hour-aligned UTC ``period_start_utc`` values
    and finite nonnegative or missing ``consumption`` values. API observations
    also require ``period_end_utc`` and one-hour ``period_hours`` values.

    Return counts and completeness for dates present in the API. Daily totals
    require all 23, 24 or 25 local hours to be paired, nonmissing and ended by
    the aware ``fetched_at`` timestamp. Compute percent error as
    100 * (API - export) / export, leaving it missing for zero export totals.
    Values remain in provider units; neither source is treated as ground truth.

    Raises:
        ValueError: Timestamp, interval or consumption validation fails.
    """
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise ValueError("fetched_at must be timezone-aware")
    api = observations.set_index("period_start_utc")
    csv = exported.set_index("period_start_utc")
    for frame in (api, csv):
        if str(frame.index.tz) != "UTC":
            raise ValueError("Comparison timestamps must be timezone-aware UTC")
        if frame.index.has_duplicates:
            raise ValueError("Duplicate UTC hours cannot be compared safely")
        if not frame.index.equals(frame.index.floor("h")):
            raise ValueError("Comparison requires UTC hour boundaries")
    if not (api["period_hours"] == 1).all():
        raise ValueError("Comparison requires hourly API observations")
    for values in (api["consumption"], csv["consumption"]):
        present = values.dropna()
        if (present < 0).any() or not present.map(lambda value: float("-inf") < value < float("inf")).all():
            raise ValueError("Consumption must be finite, nonnegative or missing")
    paired = api[["consumption", "period_end_utc"]].join(
        csv[["consumption"]], lsuffix="_api", rsuffix="_csv", how="left"
    )
    local_dates = paired.index.tz_convert("Europe/Oslo").date
    paired["valid_pair"] = paired[["consumption_api", "consumption_csv"]].notna().all(axis=1)
    paired["completed"] = paired["period_end_utc"] <= pd.Timestamp(fetched_at)
    daily = paired.groupby(local_dates).agg(
        api_hour_count=("consumption_api", "size"),
        paired_valid_hour_count=("valid_pair", "sum"),
        completed_hour_count=("completed", "sum"),
    )
    csv_counts = csv.groupby(csv.index.tz_convert("Europe/Oslo").date).size()
    daily["csv_hour_count"] = csv_counts.reindex(daily.index, fill_value=0)
    starts = pd.DatetimeIndex(daily.index).tz_localize("Europe/Oslo")
    ends = (pd.DatetimeIndex(daily.index) + pd.Timedelta(days=1)).tz_localize("Europe/Oslo")
    daily["expected_hours"] = ((ends.tz_convert("UTC") - starts.tz_convert("UTC")) / pd.Timedelta(hours=1)).astype(int)
    daily["complete"] = daily[
        ["api_hour_count", "csv_hour_count", "paired_valid_hour_count", "completed_hour_count"]
    ].eq(daily["expected_hours"], axis=0).all(axis=1)
    totals = paired.groupby(local_dates)[["consumption_api", "consumption_csv"]].sum(min_count=1)
    daily["api_consumption"] = totals["consumption_api"].where(daily["complete"])
    daily["csv_consumption"] = totals["consumption_csv"].where(daily["complete"])
    daily["consumption_difference"] = daily["api_consumption"] - daily["csv_consumption"]
    denominator = daily["csv_consumption"].where(daily["csv_consumption"] != 0)
    daily["error_percent"] = 100 * daily["consumption_difference"] / denominator
    daily.index.name = "observation_date"
    return daily.reset_index()


def plot_comparison(comparison: pd.DataFrame, destination: Path) -> None:
    """Save a yearly percent-gap chart using Matplotlib's noninteractive backend."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as dates
    import matplotlib.pyplot as plt

    plotted_dates = pd.to_datetime(comparison["observation_date"])
    years = sorted(plotted_dates.dt.year.unique())
    figure, axes = plt.subplots(len(years), 1, figsize=(12, 3 * len(years)), squeeze=False)
    maximum = comparison["error_percent"].abs().max()
    limit = max(4, int(maximum) + 2)
    colors = ["#13776f", "#b34b3e", "#466d9b"]
    for position, year in enumerate(years):
        axis = axes[position, 0]
        selected = plotted_dates.dt.year == year
        count = int(comparison.loc[selected, "error_percent"].notna().sum())
        axis.plot(plotted_dates[selected], comparison.loc[selected, "error_percent"], color=colors[position % len(colors)], linewidth=1.1)
        axis.axhline(0, color="#686868", linewidth=0.8)
        axis.set_ylim(-limit, limit)
        axis.set_ylabel("Daily total gap (%)")
        axis.set_title(f"{year} | {count} comparable Norwegian dates", loc="left", fontsize=11)
        axis.xaxis.set_major_locator(dates.MonthLocator())
        axis.xaxis.set_major_formatter(dates.DateFormatter("%b"))
        axis.grid(axis="y", color="#dedede", linewidth=0.5)
        axis.spines[["top", "right"]].set_visible(False)
        if year == 2025:
            boundary = pd.Timestamp("2025-07-09")
            axis.axvline(boundary, color="#777777", linestyle="--", linewidth=0.8)
            axis.text(boundary + pd.Timedelta(days=5), limit * 0.76, "July 9: hourly divergence begins", fontsize=9)
    figure.suptitle("Daily ingestion vs hourly data: consumption discrepancy", fontsize=15, x=0.08, ha="left")
    figure.text(0.08, 0.935, "Daily API totals agree with summed hourly API values on audited dates, but differ from summed hourly CSV exports.", fontsize=10, color="#555555")
    figure.text(0.08, 0.025, "Plotted: 100 x (summed hourly API - summed hourly CSV) / summed hourly CSV\nThe hourly API sum reproduces daily ingestion totals in the audit. Complete Oslo days only; neither source is assumed correct.", fontsize=9, color="#555555")
    figure.tight_layout(rect=(0.02, 0.075, 1, 0.92))
    figure.savefig(destination, dpi=160, facecolor="white")
    plt.close(figure)


def main() -> None:
    """Fetch annual API and CSV sources, archive them and write research outputs.

    Parse command-line years and destinations, then write the comparison CSV,
    chart and timestamped provenance JSON with source URLs, UTC fetch times,
    checksums and discrepancy statistics. No unit conversion is applied.
    Request, validation and filesystem errors propagate; earlier outputs may
    remain if a later step fails.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, nargs="+", default=[2024, 2025, 2026])
    parser.add_argument("--output-dir", type=Path, default=Path("docs/research/statnett"))
    parser.add_argument("--archive-dir", type=Path, default=Path("data/raw/statnett/comparison"))
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    if any(year < 2005 or year > now.year for year in args.years):
        parser.error("years must be between 2005 and the current year")
    args.archive_dir.mkdir(parents=True, exist_ok=True)
    comparisons = []
    provenance = []
    base = "https://driftsdata.statnett.no/restapi"
    client = StatnettClient(timeout=90)
    for year in sorted(set(args.years)):
        source = client.get_production_consumption(f"{year}-01-01", to_date=f"{year}-12-31")
        fetched_at = datetime.now(timezone.utc)
        suffix = f"{year}_{fetched_at:%Y%m%dT%H%M%S%fZ}"
        api_path = args.archive_dir / f"statnett_hourly_{suffix}.json"
        Archive(api_path).write(source)
        normalized = StatnettNormalizer().normalize(source, fetched_at=fetched_at)
        if normalized.quality.frequency != "hourly":
            raise ValueError("Provider did not return the requested hourly interval")
        csv_url = f"{base}/Download/productionconsumption/{year}?fileFormat=csv"
        with httpx.Client(timeout=90) as http_client:
            response = http_client.get(csv_url)
            response.raise_for_status()
        csv_fetched_at = datetime.now(timezone.utc)
        csv_path = args.archive_dir / f"statnett_export_{suffix}.csv"
        with csv_path.open("xb") as output:
            output.write(response.content)
        exported = pd.read_csv(io.StringIO(response.text))
        exported["period_start_utc"] = pd.to_datetime(
            exported["Time(Local)"], format="%d.%m.%Y %H:%M:%S %z", utc=True
        )
        exported = exported.rename(columns={"Consumption": "consumption"})
        comparison = compare_consumption(normalized.observations, exported, fetched_at=fetched_at)
        comparisons.append(comparison)
        errors = comparison["error_percent"].dropna()
        if errors.empty:
            raise ValueError(f"No complete comparable consumption days for {year}")
        provenance.append({
            "year": year,
            "api_url": f"{base}/ProductionConsumption/GetData",
            "frequency": "Hours",
            "requested_from_date": f"{year}-01-01",
            "requested_to_date": f"{year}-12-31 (capped at request time)",
            "api_fetched_at_utc": fetched_at.isoformat(),
            "api_archive": str(api_path),
            "api_sha256": hashlib.sha256(api_path.read_bytes()).hexdigest(),
            "csv_url": csv_url,
            "csv_fetched_at_utc": csv_fetched_at.isoformat(),
            "csv_archive": str(csv_path),
            "csv_sha256": hashlib.sha256(response.content).hexdigest(),
            "comparable_days": len(errors),
            "excluded_days": int((~comparison["complete"]).sum()),
            "mean_signed_percent": float(errors.mean()),
            "median_absolute_percent": float(errors.abs().median()),
            "p90_absolute_percent": float(errors.abs().quantile(0.9)),
            "max_absolute_percent": float(errors.abs().max()),
        })
        print(f"{year}: {len(errors)} comparable dates; median absolute difference {errors.abs().median():.5f}%")
    combined = pd.concat(comparisons, ignore_index=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    combined.to_csv(args.output_dir / "consumption_discrepancy.csv", index=False)
    plot_comparison(combined, args.output_dir / "consumption_discrepancy.png")
    Archive(args.output_dir / f"provenance_{now:%Y%m%dT%H%M%S%fZ}.json").write({
        "error_definition": "100 * (API daily sum - CSV daily sum) / CSV daily sum",
        "timezone": "Europe/Oslo",
        "units": "provider units; no conversion applied",
        "eligibility": "Complete 23/24/25-hour local days; finite paired values; all API periods ended by fetch time",
        "sources": provenance,
    })
    print(f"Saved chart and comparison data to {args.output_dir}")


if __name__ == "__main__":
    main()