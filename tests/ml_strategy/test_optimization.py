from __future__ import annotations

import numpy as np
import pandas as pd

from ml_strategy.config import StrategyConfig
from ml_strategy.optimization import build_portfolio, constrained_max_sharpe, hrp_weights, minimum_variance


def _inputs():
    dates = pd.bdate_range("2024-01-01", periods=300)
    names = [f"T{i:02d}" for i in range(12)]
    values = {
        name: 0.0002 + (i + 1) * 0.00001 + 0.005 * np.sin(np.arange(len(dates)) / (8 + i))
        for i, name in enumerate(names)
    }
    returns = pd.DataFrame(values, index=dates)
    metadata = pd.DataFrame(
        {
            "sector": [f"S{i % 4}" for i in range(12)],
            "close": [100 + i for i in range(12)],
            "lot_size": [10] * 12,
            "adv_20d": [100_000_000] * 12,
            "beta_120d": [0.8 + i * 0.02 for i in range(12)],
        },
        index=names,
    )
    forecasts = pd.Series(np.linspace(0.03, 0.005, 12), index=names)
    return returns, metadata, forecasts


def test_all_optimizers_are_long_only_and_fully_invested():
    returns, metadata, forecasts = _inputs()
    config = StrategyConfig()
    covariance = returns.cov() * 252
    sectors = metadata["sector"]
    candidates = [
        hrp_weights(returns),
        constrained_max_sharpe(forecasts, covariance, sectors, config),
        minimum_variance(covariance, sectors, config),
    ]
    for weights in candidates:
        assert (weights >= -1e-10).all()
        assert abs(weights.sum() - 1) < 1e-6


def test_executable_portfolio_obeys_weight_sector_lot_and_turnover_caps():
    returns, metadata, forecasts = _inputs()
    config = StrategyConfig()
    result = build_portfolio(forecasts, returns, metadata, pd.Series(dtype=float), config, True)
    assert result.turnover <= config.turnover_cap + 1e-9
    assert (result.executable_weights <= config.max_weight + 0.01).all()
    assert (result.shares % 10 == 0).all()
    assert result.executable_weights.sum() + result.cash_weight <= 1 + 1e-9
    sector_weights = result.executable_weights.groupby(metadata["sector"]).sum()
    assert (sector_weights <= config.sector_cap + 0.01).all()
