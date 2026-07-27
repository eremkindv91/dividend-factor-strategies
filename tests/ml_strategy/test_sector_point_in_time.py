from __future__ import annotations

import pandas as pd

from ml_strategy.sector_features.publication_calendar import point_in_time_values


def _row(value, available_at, revision_id):
    return {
        "series_id": "MONTHLY",
        "period_start": "2026-01-01T00:00:00Z",
        "period_end": "2026-01-31T00:00:00Z",
        "published_at": available_at,
        "available_at": available_at,
        "ingested_at": available_at,
        "source_updated_at": available_at,
        "source_id": "official",
        "revision_id": revision_id,
        "is_preliminary": revision_id == "v1",
        "is_revised": revision_id != "v1",
        "value": value,
    }


def test_monthly_value_is_not_visible_before_available_at():
    observations = pd.DataFrame([_row(100.0, "2026-02-20T10:00:00Z", "v1")])
    result = point_in_time_values(
        observations,
        pd.DatetimeIndex(["2026-02-19T23:59:00Z", "2026-02-20T11:00:00Z"]),
    )
    assert pd.isna(result.iloc[0]["value"])
    assert result.iloc[1]["value"] == 100.0


def test_revision_does_not_replace_historical_vintage_before_its_release():
    observations = pd.DataFrame(
        [
            _row(100.0, "2026-02-20T10:00:00Z", "v1"),
            _row(110.0, "2026-03-20T10:00:00Z", "v2"),
        ]
    )
    result = point_in_time_values(
        observations,
        pd.DatetimeIndex(["2026-03-01T00:00:00Z", "2026-03-21T00:00:00Z"]),
    )
    assert result["value"].tolist() == [100.0, 110.0]
