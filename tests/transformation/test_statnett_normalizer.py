"""Test daily and hourly Statnett mapping, validation, and fetch-time partitions."""

from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from oslo_energy.ingestion.archive import Archive
from oslo_energy.transformation.statnett_normalizer import StatnettNormalizer


def payload(start, end, production, consumption):
    """Build a legacy daily payload bounded by inclusive Oslo midnights."""
    return {
        "StartPointUTC": pd.Timestamp(start, tz="Europe/Oslo").timestamp() * 1000,
        "EndPointUTC": pd.Timestamp(end, tz="Europe/Oslo").timestamp() * 1000,
        "PeriodTickMs": 86400000,
        "Production": production,
        "Consumption": consumption,
    }


def test_autumn_padding_does_not_create_a_future_date():
    """Remove autumn null padding without adding an observation date."""
    source = payload(
        "2025-10-25", "2025-10-27",
        [306011.0, 291889.0, None, 443551.0],
        [363677.0, 370633.0, None, 389646.0],
    )

    result = StatnettNormalizer().normalize(
        source, fetched_at=datetime(2025, 10, 28, tzinfo=timezone.utc)
    )

    assert result.observations["observation_date"].astype(str).tolist() == [
        "2025-10-25", "2025-10-26", "2025-10-27"
    ]
    assert result.observations["consumption"].tolist() == [363677, 370633, 389646]
    assert result.observations["period_hours"].tolist() == [24, 25, 24]
    assert result.quality.padding_count == 1
    assert result.quality.raw_slot_count == 4
    assert result.quality.observation_count == 3


def test_non_null_autumn_padding_is_rejected():
    """Reject a non-null measurement in an expected autumn padding slot."""
    source = payload("2025-10-26", "2025-10-27", [1, 2, 3], [4, None, 5])

    with pytest.raises(ValueError, match="padding"):
        StatnettNormalizer().normalize(
            source, fetched_at=datetime(2025, 10, 28, tzinfo=timezone.utc)
        )


def test_spring_day_has_23_hours_and_no_padding():
    """Map the spring Oslo day to 23 elapsed hours with correct UTC boundaries."""
    source = payload("2025-03-29", "2025-03-31", [1, 2, 3], [4, 5, 6])

    result = StatnettNormalizer().normalize(
        source, fetched_at=datetime(2025, 4, 1, tzinfo=timezone.utc)
    )

    assert result.observations["period_hours"].tolist() == [24, 23, 24]
    assert result.quality.padding_count == 0
    starts = result.observations["period_start_utc"]
    assert starts.iloc[1] == pd.Timestamp("2025-03-29T23:00:00Z")
    assert starts.iloc[2] == pd.Timestamp("2025-03-30T22:00:00Z")
    assert str(starts.dt.tz) == "UTC"
    assert starts.dt.tz_convert("Europe/Oslo").dt.date.tolist() == (
        result.observations["observation_date"].tolist()
    )


def test_genuine_missing_values_remain_missing_without_mutating_source():
    """Preserve genuine nulls and source data while reporting missing-value rates."""
    source = payload("2025-01-01", "2025-01-03", [None, 2, 3], [4, 5, None])
    original = deepcopy(source)

    result = StatnettNormalizer().normalize(
        source, fetched_at=datetime(2025, 1, 4, tzinfo=timezone.utc)
    )

    assert source == original
    assert pd.isna(result.historical.iloc[0]["production"])
    assert pd.isna(result.historical.iloc[2]["consumption"])
    assert result.quality.production_missing_count == 1
    assert result.quality.consumption_missing_count == 1
    assert result.quality.production_missing_rate == pytest.approx(1 / 3)
    assert result.quality.consumption_missing_rate == pytest.approx(1 / 3)
    assert result.quality.duplicate_count == 0
    assert result.quality.temporal_gap_count == 0


def test_incomplete_and_future_dates_are_not_historical():
    """Partition daily observations by the fetch timestamp's Oslo calendar date."""
    source = payload("2025-01-01", "2025-01-03", [1, 2, 3], [4, 5, 6])

    result = StatnettNormalizer().normalize(
        source, fetched_at=datetime(2025, 1, 1, 23, 30, tzinfo=timezone.utc)
    )

    assert result.historical["observation_date"].tolist() == [date(2025, 1, 1)]
    assert result.incomplete["observation_date"].tolist() == [date(2025, 1, 2)]
    assert result.forecast["observation_date"].tolist() == [date(2025, 1, 3)]
    assert result.quality.historical_count == 1
    assert result.quality.incomplete_count == 1
    assert result.quality.forecast_count == 1


def test_autumn_range_start_keeps_first_measurement():
    """Keep the first autumn-day measurement and skip only its following padding."""
    source = payload("2025-10-26", "2025-10-27", [2, None, 3], [5, None, 6])

    result = StatnettNormalizer().normalize(
        source, fetched_at=datetime(2025, 10, 28, tzinfo=timezone.utc)
    )

    assert result.historical["source_index"].tolist() == [0, 2]
    assert result.historical["consumption"].tolist() == [5, 6]
    assert result.quality.padding_dates == ("2025-10-26",)


def test_missing_measurement_on_autumn_day_is_not_padding():
    """Distinguish a missing autumn-day measurement from its null padding slot."""
    source = payload("2025-10-26", "2025-10-27", [None, None, 3], [None, None, 6])

    result = StatnettNormalizer().normalize(
        source, fetched_at=datetime(2025, 10, 28, tzinfo=timezone.utc)
    )

    assert len(result.historical) == 2
    assert pd.isna(result.historical.iloc[0]["consumption"])
    assert result.quality.padding_count == 1
    assert result.quality.consumption_missing_count == 1


@pytest.mark.parametrize("period", [0, -1, 900000, True, "86400000", None])
def test_unsupported_period_is_rejected(period):
    """Reject unsupported interval lengths and invalid period metadata types."""
    source = payload("2025-01-01", "2025-01-01", [1], [2])
    source["PeriodTickMs"] = period

    with pytest.raises(ValueError, match="daily"):
        StatnettNormalizer().normalize(
            source, fetched_at=datetime(2025, 1, 2, tzinfo=timezone.utc)
        )


@pytest.mark.parametrize("field", ["Production", "Consumption"])
@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True, "1"])
def test_invalid_measurements_are_rejected(field, value):
    """Reject negative, nonfinite, boolean, and string measurements."""
    source = payload("2025-01-01", "2025-01-01", [1], [2])
    source[field] = [value]

    with pytest.raises(ValueError, match="nonnegative finite"):
        StatnettNormalizer().normalize(
            source, fetched_at=datetime(2025, 1, 2, tzinfo=timezone.utc)
        )


@pytest.mark.parametrize("field", ["StartPointUTC", "EndPointUTC"])
@pytest.mark.parametrize("value", [None, True, "0", float("nan"), 1.5])
def test_invalid_timestamp_metadata_is_rejected(field, value):
    """Reject timestamp metadata that is not finite integral Unix milliseconds."""
    source = payload("2025-01-01", "2025-01-01", [1], [2])
    source[field] = value

    with pytest.raises(ValueError, match="Unix milliseconds"):
        StatnettNormalizer().normalize(
            source, fetched_at=datetime(2025, 1, 2, tzinfo=timezone.utc)
        )


def test_metadata_must_identify_local_midnight():
    """Reject daily endpoints that do not identify Oslo local midnight."""
    source = payload("2025-01-01", "2025-01-01", [1], [2])
    source["StartPointUTC"] += 3600000

    with pytest.raises(ValueError, match="local midnight"):
        StatnettNormalizer().normalize(
            source, fetched_at=datetime(2025, 1, 2, tzinfo=timezone.utc)
        )


def test_naive_fetch_timestamp_is_rejected():
    """Require a timezone-aware timestamp for the normalization cutoff."""
    source = payload("2025-01-01", "2025-01-01", [1], [2])

    with pytest.raises(ValueError, match="timezone-aware"):
        StatnettNormalizer().normalize(source, fetched_at=datetime(2025, 1, 2))


@pytest.mark.parametrize(
    "production,consumption,message",
    [([1], [2, 3], "lengths differ"), ([1, 2], [3, 4], "calendar contract"),
     ([], [], "calendar contract"), ({}, [], "array")],
)
def test_malformed_arrays_fail_loudly(production, consumption, message):
    """Reject non-array measurements and lengths inconsistent with daily metadata."""
    source = payload("2025-01-01", "2025-01-01", production, consumption)

    with pytest.raises(ValueError, match=message):
        StatnettNormalizer().normalize(
            source, fetched_at=datetime(2025, 1, 2, tzinfo=timezone.utc)
        )


def test_missing_field_is_rejected():
    """Reject payloads missing a required metadata field."""
    source = payload("2025-01-01", "2025-01-01", [1], [2])
    del source["EndPointUTC"]

    with pytest.raises(ValueError, match="required fields"):
        StatnettNormalizer().normalize(
            source, fetched_at=datetime(2025, 1, 2, tzinfo=timezone.utc)
        )


def test_reversed_timestamp_range_is_rejected():
    """Reject an end timestamp earlier than the start timestamp."""
    source = payload("2025-01-02", "2025-01-01", [1], [2])

    with pytest.raises(ValueError, match="precedes"):
        StatnettNormalizer().normalize(
            source, fetched_at=datetime(2025, 1, 3, tzinfo=timezone.utc)
        )


@pytest.mark.parametrize(
    "filename", ["consumption.json", "consumption_2023-01-01.json", "consumption_2025-01-01.json"]
)
def test_existing_provider_examples_reconcile_without_changing_values(filename):
    """Reconcile archived example slots while retaining source values and indices."""
    path = Path(__file__).resolve().parents[2] / "data/examples/statnett" / filename
    source = Archive(path).read()
    original = deepcopy(source)
    fetched_at = (
        pd.to_datetime(source["EndPointUTC"], unit="ms", utc=True)
        + pd.Timedelta(days=1)
    ).to_pydatetime()

    result = StatnettNormalizer().normalize(source, fetched_at=fetched_at)

    assert source == original
    assert len(source["Consumption"]) == len(result.observations) + result.quality.padding_count
    assert result.historical["observation_date"].is_unique
    assert result.quality.incomplete_count == 0
    assert result.quality.forecast_count == 0
    for index, value in zip(result.observations["source_index"], result.observations["consumption"]):
        expected = source["Consumption"][index]
        assert pd.isna(value) if expected is None else value == expected


def hourly_payload(start, count):
    """Build a contiguous hourly payload with inclusive UTC endpoints."""
    starts = pd.date_range(start=pd.Timestamp(start), periods=count, freq="h")
    return {
        "StartPointUTC": int(starts[0].timestamp() * 1000),
        "EndPointUTC": int(starts[-1].timestamp() * 1000),
        "PeriodTickMs": 3600000,
        "Production": list(range(count)),
        "Consumption": list(range(count)),
    }


@pytest.mark.parametrize(
    "start,count,local_date", [("2025-03-29T23:00:00Z", 23, "2025-03-30"),
                              ("2025-10-25T22:00:00Z", 25, "2025-10-26")],
)
def test_hourly_dst_days_have_unique_utc_periods(start, count, local_date):
    """Keep unique UTC hours across Oslo DST days without marking training ready."""
    source = hourly_payload(start, count)
    original = deepcopy(source)
    result = StatnettNormalizer().normalize(
        source, fetched_at=datetime(2025, 10, 28, tzinfo=timezone.utc)
    )
    assert source == original
    assert len(result.observations) == count
    assert result.observations["observation_date"].astype(str).unique().tolist() == [local_date]
    assert result.observations["period_start_utc"].is_unique
    assert result.observations["period_hours"].tolist() == [1] * count
    assert result.quality.frequency == "hourly"
    assert result.quality.period_tick_ms == 3600000
    assert result.quality.expected_date_count == 1
    assert result.quality.padding_count == result.quality.duplicate_count == 0
    assert result.quality.temporal_gap_count == 0
    assert result.quality.training_ready is False
    if count == 25:
        local_hours = result.observations["period_start_utc"].dt.tz_convert("Europe/Oslo").dt.hour
        assert (local_hours == 2).sum() == 2


def test_hourly_missing_values_and_fetch_cutoff():
    """Preserve hourly nulls while partitioning completed, ongoing, and future slots."""
    source = hourly_payload("2025-01-01T00:00:00Z", 3)
    source["Consumption"][0] = None
    result = StatnettNormalizer().normalize(
        source, fetched_at=datetime(2025, 1, 1, 1, 30, tzinfo=timezone.utc)
    )
    assert result.historical["source_index"].tolist() == [0]
    assert result.incomplete["source_index"].tolist() == [1]
    assert result.forecast["source_index"].tolist() == [2]
    assert pd.isna(result.historical["consumption"].iloc[0])
    assert result.quality.consumption_missing_count == 1
    assert result.quality.consumption_missing_rate == pytest.approx(1 / 3)


def test_hourly_period_ending_at_fetch_time_is_completed():
    """Treat an hour ending at fetch time as completed and the next as ongoing."""
    result = StatnettNormalizer().normalize(
        hourly_payload("2025-01-01T00:00:00Z", 2),
        fetched_at=datetime(2025, 1, 1, 1, tzinfo=timezone.utc),
    )
    assert result.historical["source_index"].tolist() == [0]
    assert result.incomplete["source_index"].tolist() == [1]
    assert result.quality.forecast_count == 0


@pytest.mark.parametrize("field", ["StartPointUTC", "EndPointUTC"])
def test_hourly_unaligned_endpoint_is_rejected(field):
    """Reject hourly endpoints that are not aligned to UTC hour boundaries."""
    source = hourly_payload("2025-01-01T00:00:00Z", 3)
    source[field] += 60000
    with pytest.raises(ValueError, match="hour boundaries"):
        StatnettNormalizer().normalize(
            source, fetched_at=datetime(2025, 1, 2, tzinfo=timezone.utc)
        )


def test_hourly_slot_count_must_match_metadata():
    """Require hourly array lengths to match the inclusive endpoint range."""
    source = hourly_payload("2025-01-01T00:00:00Z", 3)
    source["Production"].pop()
    source["Consumption"].pop()
    with pytest.raises(ValueError, match="Hourly calendar contract"):
        StatnettNormalizer().normalize(
            source, fetched_at=datetime(2025, 1, 2, tzinfo=timezone.utc)
        )