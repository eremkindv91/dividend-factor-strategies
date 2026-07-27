from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ml_strategy.sector_features.transformations import expanding_zscore, trailing_return


def test_expanding_zscore_uses_only_prior_observations():
    values = pd.Series([1.0, 2.0, 3.0, 100.0])
    before = expanding_zscore(values, min_periods=2)
    changed_future = values.copy()
    changed_future.iloc[-1] = -100.0
    after = expanding_zscore(changed_future, min_periods=2)
    assert np.allclose(before.iloc[:-1], after.iloc[:-1], equal_nan=True)


def test_trailing_return_preserves_dimensionless_units():
    values = pd.Series([100.0, 110.0])
    assert trailing_return(values, 1).iloc[-1] == pytest.approx(0.1)
