from __future__ import annotations

import numpy as np
import pandas as pd


def expanding_zscore(values: pd.Series, min_periods: int = 60) -> pd.Series:
    mean = values.expanding(min_periods=min_periods).mean().shift(1)
    std = values.expanding(min_periods=min_periods).std(ddof=1).shift(1)
    return values.sub(mean).div(std.replace(0, np.nan))


def trailing_return(values: pd.Series, periods: int) -> pd.Series:
    return values.div(values.shift(periods)).sub(1)


def trailing_volatility(values: pd.Series, periods: int = 20) -> pd.Series:
    returns = values.pct_change(fill_method=None)
    return returns.rolling(periods, min_periods=periods).std(ddof=1).mul(np.sqrt(252.0))


def stale_days(available_at: pd.Series, dates: pd.DatetimeIndex) -> pd.Series:
    available = pd.to_datetime(available_at, utc=True).dt.tz_convert(None)
    date_series = pd.Series(pd.to_datetime(dates), index=dates)
    aligned = pd.Series(available.to_numpy(), index=dates)
    return date_series.sub(aligned).dt.days.astype(float)
