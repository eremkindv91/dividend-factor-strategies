from __future__ import annotations

from ml_strategy.config import StrategyConfig
from ml_strategy.data import load_market_data
from ml_strategy.features import build_feature_panel
from ml_strategy.models import walk_forward


def test_walk_forward_purges_open_targets(market_repo):
    config = StrategyConfig(
        min_training_rows=300,
        evaluation_folds=5,
        max_universe=18,
        min_cross_section=10,
    )
    data = load_market_data(
        market_repo / "data" / "daily",
        market_repo / "data" / "security_master.json",
        market_repo / "data" / "daily" / "benchmarks" / "MCFTR.parquet",
        market_repo / "data" / "daily" / "dividends.json",
    )
    result = walk_forward(build_feature_panel(data, config), config)
    assert len(result.folds) >= 3
    assert all(fold["purge_rule"] == "target_end_date < prediction_date" for fold in result.folds)
    assert {"zero", "historical_mean", "momentum_12_1", "elastic_net"} <= set(result.metrics)
    assert result.latest_forecasts.notna().all()
