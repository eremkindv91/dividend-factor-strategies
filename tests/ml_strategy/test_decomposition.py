from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ml_strategy.decomposition import IceemdanUnavailable, expanding_iceemdan_features


def test_iceemdan_adapter_never_receives_future_values(tmp_path):
    dates = pd.bdate_range("2023-01-02", periods=420)
    series = pd.Series(np.arange(420, dtype=float), index=dates)
    seen_last_values = []

    def backend(values, seed):
        seen_last_values.append((values[-1], seed))
        return {"iceemdan_trend_slope": float(values[-1] - values[-20])}

    prediction_dates = [dates[300], dates[350]]
    result = expanding_iceemdan_features(series, prediction_dates, backend, tmp_path, seed=17)
    assert list(result.index) == prediction_dates
    assert seen_last_values == [(300.0, 17), (350.0, 17)]


def test_iceemdan_is_blocked_without_an_audited_backend():
    series = pd.Series(np.arange(300, dtype=float), index=pd.bdate_range("2024-01-01", periods=300))
    with pytest.raises(IceemdanUnavailable):
        expanding_iceemdan_features(series, [series.index[-1]], None)
