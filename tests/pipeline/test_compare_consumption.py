from datetime import datetime, timezone

import pandas as pd
import pytest

from oslo_energy.pipeline.compare_consumption import compare_consumption


def comparison_inputs(start, count):
    periods = pd.date_range(start, periods=count, freq="h", tz="UTC")
    api = pd.DataFrame({
        "period_start_utc": periods,
        "period_end_utc": periods + pd.Timedelta(hours=1),
        "period_hours": 1,
        "consumption": 110.0,
    })
    exported = pd.DataFrame({"period_start_utc": periods, "consumption": 100.0})
    return api, exported


@pytest.mark.parametrize("start,count", [("2025-03-29 23:00", 23), ("2025-10-25 22:00", 25)])
def test_comparison_uses_complete_local_dst_days(start, count):
    api, exported = comparison_inputs(start, count)
    result = compare_consumption(api, exported, fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert result["expected_hours"].tolist() == [count]
    assert result["complete"].tolist() == [True]
    assert result["error_percent"].tolist() == [10.0]
    assert result["consumption_difference"].tolist() == [count * 10]


@pytest.mark.parametrize("problem", ["api_null", "csv_null", "csv_gap", "unfinished"])
def test_partial_days_are_not_given_an_error_total(problem):
    api, exported = comparison_inputs("2025-01-01 23:00", 24)
    fetched_at = datetime(2025, 1, 3, tzinfo=timezone.utc)
    if problem == "api_null":
        api.loc[3, "consumption"] = float("nan")
    elif problem == "csv_null":
        exported.loc[3, "consumption"] = float("nan")
    elif problem == "csv_gap":
        exported = exported.drop(index=3)
    else:
        fetched_at = datetime(2025, 1, 2, 22, 30, tzinfo=timezone.utc)
    result = compare_consumption(api, exported, fetched_at=fetched_at)
    assert result["complete"].tolist() == [False]
    assert result["error_percent"].isna().all()
    assert result["api_consumption"].isna().all()
    assert result["csv_consumption"].isna().all()


def test_duplicate_export_hours_are_rejected():
    api, exported = comparison_inputs("2025-01-01 23:00", 24)
    exported = pd.concat([exported, exported.iloc[:1]])
    with pytest.raises(ValueError, match="Duplicate UTC"):
        compare_consumption(api, exported, fetched_at=datetime(2025, 1, 3, tzinfo=timezone.utc))


def test_zero_export_denominator_does_not_produce_infinite_error():
    api, exported = comparison_inputs("2025-01-01 23:00", 24)
    exported["consumption"] = 0.0
    result = compare_consumption(api, exported, fetched_at=datetime(2025, 1, 3, tzinfo=timezone.utc))
    assert result["complete"].tolist() == [True]
    assert result["error_percent"].isna().all()