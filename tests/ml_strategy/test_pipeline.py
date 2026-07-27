from __future__ import annotations

import json
from datetime import date

from ml_strategy.config import StrategyConfig
from ml_strategy.pipeline import run_pipeline
from ml_strategy.schemas import validate_bundle


def test_pipeline_writes_atomic_valid_snapshots(market_repo):
    config = StrategyConfig(
        min_training_rows=300,
        evaluation_folds=5,
        max_universe=18,
        min_cross_section=10,
    )
    bundle = run_pipeline(market_repo, config=config, today=date(2024, 12, 1))
    assert bundle["latest.json"]["portfolio"]["positions"]
    assert bundle["latest.json"]["benchmark"] == "MCFTR"
    assert bundle["data_quality.json"]["production_data"] == "real_sources_only"
    assert not validate_bundle(market_repo / "site" / "ml_strategy")
    latest = json.loads((market_repo / "data" / "ml_strategy" / "latest.json").read_text())
    history = market_repo / "data" / "ml_strategy" / "history" / f"{latest['data_as_of']}.json"
    assert history.exists()
    assert bundle["backtest.json"]["portfolio_metrics"]["average_turnover"] <= config.turnover_cap + 1e-9


def test_pipeline_keeps_last_good_when_input_is_blocked(market_repo):
    config = StrategyConfig(
        min_training_rows=300,
        evaluation_folds=5,
        max_universe=18,
        min_cross_section=10,
    )
    run_pipeline(market_repo, config=config, today=date(2024, 12, 1))
    path = market_repo / "site" / "ml_strategy" / "latest.json"
    before = path.read_bytes()
    for price in (market_repo / "data" / "daily" / "prices").glob("*.parquet"):
        price.unlink()
    try:
        run_pipeline(market_repo, config=config, today=date(2024, 12, 1))
    except ValueError:
        pass
    else:
        raise AssertionError("blocked input must fail")
    assert path.read_bytes() == before
