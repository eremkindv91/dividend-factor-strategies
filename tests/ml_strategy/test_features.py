from __future__ import annotations

import pandas as pd

from ml_strategy.config import StrategyConfig
from ml_strategy.data import load_market_data
from ml_strategy.features import build_feature_panel


def test_target_is_forward_and_has_explicit_end_date(market_repo):
    data = load_market_data(
        market_repo / "data" / "daily",
        market_repo / "data" / "security_master.json",
        market_repo / "data" / "daily" / "benchmarks" / "MCFTR.parquet",
        market_repo / "data" / "daily" / "dividends.json",
    )
    panel = build_feature_panel(data, StrategyConfig())
    rows = panel.dropna(subset=["forward_excess_total_return_20d_vs_mcftr", "target_end_date"])
    dates = rows.index.get_level_values("date")
    assert (pd.to_datetime(rows["target_end_date"]).to_numpy() > dates.to_numpy()).all()
    assert rows["forward_total_return_20d"].notna().all()


def test_feature_panel_uses_real_source_columns(market_repo):
    data = load_market_data(
        market_repo / "data" / "daily",
        market_repo / "data" / "security_master.json",
        market_repo / "data" / "daily" / "benchmarks" / "MCFTR.parquet",
        market_repo / "data" / "daily" / "dividends.json",
    )
    panel = build_feature_panel(data, StrategyConfig())
    latest = panel.xs(data.as_of, level="date")
    assert latest["adv_20d"].notna().sum() == 18
    assert latest["beta_120d"].notna().sum() == 18
    assert latest["mcftr_return_20d"].nunique() == 1
    assert latest["key_rate_pct"].iloc[0] == 0.12


def test_forward_target_has_no_same_day_return(market_repo):
    data = load_market_data(
        market_repo / "data" / "daily",
        market_repo / "data" / "security_master.json",
        market_repo / "data" / "daily" / "benchmarks" / "MCFTR.parquet",
        market_repo / "data" / "daily" / "dividends.json",
    )
    config = StrategyConfig()
    panel = build_feature_panel(data, config)
    date = data.close.index[400]
    end = data.close.index[400 + config.horizon]
    expected_total = data.close.at[end, "T10"] / data.close.at[date, "T10"] - 1
    expected_benchmark = data.benchmark.at[end] / data.benchmark.at[date] - 1
    row = panel.loc[(date, "T10")]
    assert abs(row["forward_total_return_20d"] - expected_total) < 1e-10
    assert abs(row["forward_excess_total_return_20d_vs_mcftr"] - (expected_total - expected_benchmark)) < 1e-10
