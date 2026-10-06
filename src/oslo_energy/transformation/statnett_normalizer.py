"""Map the daily national Statnett series to Norwegian calendar dates."""

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class StatnettQualityReport:
    raw_slot_count: int
    expected_date_count: int
    observation_count: int
    padding_count: int
    padding_dates: tuple[str, ...]
    production_missing_count: int
    consumption_missing_count: int
    production_missing_rate: float
    consumption_missing_rate: float
    historical_count: int
    incomplete_count: int
    forecast_count: int
    duplicate_count: int
    temporal_gap_count: int
    source_start_date: str
    source_end_date: str
    fetched_at_utc: str
    timezone: str = "Europe/Oslo"
    geographic_scope: str = "Norway"
    measurement_units: str = "provider units; daily aggregation not independently verified"
    reference_validation: str = "daily API totals have not been reconciled with hourly CSV"
    training_ready: bool = False


@dataclass(frozen=True)
class NormalizedStatnettData:
    observations: pd.DataFrame
    historical: pd.DataFrame
    incomplete: pd.DataFrame
    forecast: pd.DataFrame
    quality: StatnettQualityReport


class StatnettNormalizer:
    timezone = "Europe/Oslo"
    daily_period_ms = 86400000

    def normalize(
        self, data: dict[str, Any], *, fetched_at: datetime
    ) -> NormalizedStatnettData:
        required = {
            "StartPointUTC", "EndPointUTC", "PeriodTickMs", "Production", "Consumption"
        }
        if not isinstance(data, dict) or not required.issubset(data):
            raise ValueError("Statnett response is missing required fields")
        if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        if isinstance(data["PeriodTickMs"], bool) or data["PeriodTickMs"] != self.daily_period_ms:
            raise ValueError("Only the daily Statnett interval (86400000 ms) is supported")

        start = self._local_midnight(data["StartPointUTC"], "StartPointUTC")
        end = self._local_midnight(data["EndPointUTC"], "EndPointUTC")
        if end < start:
            raise ValueError("EndPointUTC precedes StartPointUTC")

        production = self._measurements(data["Production"], "Production")
        consumption = self._measurements(data["Consumption"], "Consumption")
        if len(production) != len(consumption):
            raise ValueError("Production and Consumption lengths differ")

        boundaries = pd.date_range(
            start=start, end=end + pd.DateOffset(days=1), freq="D"
        )
        period_starts = boundaries[:-1]
        period_ends = boundaries[1:]
        hours = (period_ends.tz_convert("UTC") - period_starts.tz_convert("UTC")) / pd.Timedelta(hours=1)
        expected_slots = len(period_starts) + int((hours == 25).sum())
        if len(production) != expected_slots:
            raise ValueError(
                f"Daily calendar contract requires {expected_slots} slots "
                f"({len(period_starts)} dates plus autumn padding); received {len(production)}"
            )

        source_indices = []
        padding_dates = []
        position = 0
        for timestamp, period_hours in zip(period_starts, hours):
            source_indices.append(position)
            position += 1
            if period_hours == 25:
                if production[position] is not None or consumption[position] is not None:
                    raise ValueError(
                        f"Expected null autumn padding at source index {position} "
                        f"after {timestamp.date()}"
                    )
                padding_dates.append(timestamp.date().isoformat())
                position += 1

        observations = pd.DataFrame({
            "observation_date": period_starts.date,
            "period_start_utc": period_starts.tz_convert("UTC"),
            "period_end_utc": period_ends.tz_convert("UTC"),
            "period_hours": hours.astype(int),
            "source_index": source_indices,
            "production": pd.Series([production[index] for index in source_indices], dtype="float64"),
            "consumption": pd.Series([consumption[index] for index in source_indices], dtype="float64"),
        })
        fetch_time = pd.Timestamp(fetched_at).tz_convert(self.timezone)
        historical = observations.loc[observations["observation_date"] < fetch_time.date()].copy()
        incomplete = observations.loc[observations["observation_date"] == fetch_time.date()].copy()
        forecast = observations.loc[observations["observation_date"] > fetch_time.date()].copy()
        production_missing = int(observations["production"].isna().sum())
        consumption_missing = int(observations["consumption"].isna().sum())
        quality = StatnettQualityReport(
            raw_slot_count=len(production),
            expected_date_count=len(period_starts),
            observation_count=len(observations),
            padding_count=len(padding_dates),
            padding_dates=tuple(padding_dates),
            production_missing_count=production_missing,
            consumption_missing_count=consumption_missing,
            production_missing_rate=production_missing / len(observations),
            consumption_missing_rate=consumption_missing / len(observations),
            historical_count=len(historical),
            incomplete_count=len(incomplete),
            forecast_count=len(forecast),
            duplicate_count=int(observations["observation_date"].duplicated().sum()),
            temporal_gap_count=int((observations["observation_date"].diff().dropna() != pd.Timedelta(days=1)).sum()),
            source_start_date=start.date().isoformat(),
            source_end_date=end.date().isoformat(),
            fetched_at_utc=fetch_time.tz_convert("UTC").isoformat(),
        )
        return NormalizedStatnettData(observations, historical, incomplete, forecast, quality)

    def _local_midnight(self, value: Any, field: str) -> pd.Timestamp:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value != int(value)
        ):
            raise ValueError(f"{field} must be finite Unix milliseconds")
        try:
            timestamp = pd.to_datetime(value, unit="ms", utc=True).tz_convert(self.timezone)
        except (ValueError, OverflowError) as exc:
            raise ValueError(f"{field} is not a supported timestamp") from exc
        if timestamp != timestamp.normalize():
            raise ValueError(f"{field} must identify Norwegian local midnight")
        return timestamp

    @staticmethod
    def _measurements(values: Any, field: str) -> list:
        if not isinstance(values, list):
            raise ValueError(f"{field} must be an array")
        for value in values:
            if value is None:
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"{field} must contain nonnegative finite numbers or null")
        return values