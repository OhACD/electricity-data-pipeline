"""Map hourly Statnett data to UTC periods; retain legacy daily replay."""

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
    frequency: str = "daily"
    period_tick_ms: int = 86400000
    measurement_units: str = "provider units; independent unit validation required"
    reference_validation: str = "API/export differences and source finality remain unresolved"
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
    hourly_period_ms = 3600000

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
        if isinstance(data["PeriodTickMs"], bool) or data["PeriodTickMs"] not in (
            self.daily_period_ms, self.hourly_period_ms
        ):
            raise ValueError("Only hourly (3600000 ms) and legacy daily (86400000 ms) intervals are supported")
        if data["PeriodTickMs"] == self.hourly_period_ms:
            return self._normalize_hourly(data, fetched_at=fetched_at)

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
        return self._result(
            observations, fetched_at=fetched_at, raw_slot_count=len(production),
            padding_dates=tuple(padding_dates), frequency="daily",
        )

    def _normalize_hourly(
        self, data: dict[str, Any], *, fetched_at: datetime
    ) -> NormalizedStatnettData:
        start = self._timestamp(data["StartPointUTC"], "StartPointUTC")
        end = self._timestamp(data["EndPointUTC"], "EndPointUTC")
        if end < start:
            raise ValueError("EndPointUTC precedes StartPointUTC")
        if start != start.floor("h") or end != end.floor("h"):
            raise ValueError("Hourly endpoints must identify UTC hour boundaries")
        production = self._measurements(data["Production"], "Production")
        consumption = self._measurements(data["Consumption"], "Consumption")
        if len(production) != len(consumption):
            raise ValueError("Production and Consumption lengths differ")
        period_starts = pd.date_range(start=start, end=end, freq="h")
        if len(production) != len(period_starts):
            raise ValueError(
                f"Hourly calendar contract requires {len(period_starts)} slots; received {len(production)}"
            )
        observations = pd.DataFrame({
            "observation_date": period_starts.tz_convert(self.timezone).date,
            "period_start_utc": period_starts,
            "period_end_utc": period_starts + pd.Timedelta(hours=1),
            "period_hours": 1,
            "source_index": range(len(production)),
            "production": pd.Series(production, dtype="float64"),
            "consumption": pd.Series(consumption, dtype="float64"),
        })
        return self._result(
            observations, fetched_at=fetched_at, raw_slot_count=len(production),
            padding_dates=(), frequency="hourly",
        )

    def _result(
        self, observations: pd.DataFrame, *, fetched_at: datetime,
        raw_slot_count: int, padding_dates: tuple[str, ...], frequency: str,
    ) -> NormalizedStatnettData:
        fetch_time = pd.Timestamp(fetched_at).tz_convert(self.timezone)
        if frequency == "hourly":
            historical = observations.loc[observations["period_end_utc"] <= fetch_time].copy()
            incomplete = observations.loc[
                (observations["period_start_utc"] <= fetch_time)
                & (observations["period_end_utc"] > fetch_time)
            ].copy()
            forecast = observations.loc[observations["period_start_utc"] > fetch_time].copy()
            identity = observations["period_start_utc"]
            interval = pd.Timedelta(hours=1)
        else:
            historical = observations.loc[observations["observation_date"] < fetch_time.date()].copy()
            incomplete = observations.loc[observations["observation_date"] == fetch_time.date()].copy()
            forecast = observations.loc[observations["observation_date"] > fetch_time.date()].copy()
            identity = observations["observation_date"]
            interval = pd.Timedelta(days=1)
        production_missing = int(observations["production"].isna().sum())
        consumption_missing = int(observations["consumption"].isna().sum())
        quality = StatnettQualityReport(
            raw_slot_count=raw_slot_count,
            expected_date_count=int(observations["observation_date"].nunique()),
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
            duplicate_count=int(identity.duplicated().sum()),
            temporal_gap_count=int((identity.diff().dropna() != interval).sum()),
            source_start_date=observations["observation_date"].iloc[0].isoformat(),
            source_end_date=observations["observation_date"].iloc[-1].isoformat(),
            fetched_at_utc=fetch_time.tz_convert("UTC").isoformat(),
            frequency=frequency,
            period_tick_ms=self.hourly_period_ms if frequency == "hourly" else self.daily_period_ms,
        )
        return NormalizedStatnettData(observations, historical, incomplete, forecast, quality)

    def _local_midnight(self, value: Any, field: str) -> pd.Timestamp:
        timestamp = self._timestamp(value, field).tz_convert(self.timezone)
        if timestamp != timestamp.normalize():
            raise ValueError(f"{field} must identify Norwegian local midnight")
        return timestamp

    @staticmethod
    def _timestamp(value: Any, field: str) -> pd.Timestamp:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value != int(value)
        ):
            raise ValueError(f"{field} must be finite Unix milliseconds")
        try:
            timestamp = pd.to_datetime(value, unit="ms", utc=True)
        except (ValueError, OverflowError) as exc:
            raise ValueError(f"{field} is not a supported timestamp") from exc
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