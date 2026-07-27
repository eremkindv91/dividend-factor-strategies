from __future__ import annotations

import pandas as pd


OBSERVATION_COLUMNS = (
    "series_id",
    "period_start",
    "period_end",
    "published_at",
    "available_at",
    "ingested_at",
    "source_updated_at",
    "source_id",
    "revision_id",
    "is_preliminary",
    "is_revised",
    "value",
)


def validate_observations(frame: pd.DataFrame) -> list[str]:
    errors = [f"missing {column}" for column in OBSERVATION_COLUMNS if column not in frame]
    if errors:
        return errors
    for column in ("period_start", "period_end", "published_at", "available_at", "ingested_at"):
        values = pd.to_datetime(frame[column], errors="coerce", utc=True)
        if values.isna().any():
            errors.append(f"{column} contains invalid timestamps")
    available = pd.to_datetime(frame["available_at"], errors="coerce", utc=True)
    published = pd.to_datetime(frame["published_at"], errors="coerce", utc=True)
    if (available < published).any():
        errors.append("available_at precedes published_at")
    return errors


def point_in_time_values(
    observations: pd.DataFrame,
    prediction_times: pd.DatetimeIndex,
) -> pd.DataFrame:
    errors = validate_observations(observations)
    if errors:
        raise ValueError("; ".join(errors))
    obs = observations.copy()
    obs["available_at"] = pd.to_datetime(obs["available_at"], utc=True)
    obs["ingested_at"] = pd.to_datetime(obs["ingested_at"], utc=True)
    obs = obs.sort_values(["series_id", "available_at", "ingested_at", "revision_id"])
    query = pd.DataFrame({"prediction_at": pd.to_datetime(prediction_times, utc=True)})
    blocks: list[pd.DataFrame] = []
    for series_id, rows in obs.groupby("series_id", sort=True):
        available = rows.drop_duplicates("available_at", keep="last")
        aligned = pd.merge_asof(
            query.sort_values("prediction_at"),
            available[["available_at", "value", "revision_id"]].sort_values("available_at"),
            left_on="prediction_at",
            right_on="available_at",
            direction="backward",
        )
        aligned["series_id"] = series_id
        blocks.append(aligned)
    if not blocks:
        return pd.DataFrame(columns=["prediction_at", "series_id", "available_at", "value", "revision_id"])
    return pd.concat(blocks, ignore_index=True)


def market_series_observations(
    series_id: str,
    values: pd.Series,
    availability_lag_calendar_days: int,
    ingested_at: pd.Timestamp,
) -> pd.DataFrame:
    clean = pd.to_numeric(values, errors="coerce").dropna().sort_index()
    periods = pd.to_datetime(clean.index, utc=True)
    available = periods + pd.to_timedelta(availability_lag_calendar_days, unit="D")
    return pd.DataFrame(
        {
            "series_id": series_id,
            "period_start": periods,
            "period_end": periods,
            "published_at": available,
            "available_at": available,
            "ingested_at": pd.Timestamp(ingested_at).tz_convert("UTC"),
            "source_updated_at": available,
            "source_id": series_id,
            "revision_id": [f"{series_id}:{stamp.date().isoformat()}:v1" for stamp in periods],
            "is_preliminary": False,
            "is_revised": False,
            "value": clean.to_numpy(dtype=float),
        }
    )
