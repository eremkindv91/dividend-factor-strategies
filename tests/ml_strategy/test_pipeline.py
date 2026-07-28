from __future__ import annotations

import json
from datetime import date

from ml_strategy.config import StrategyConfig
from ml_strategy.pipeline import run_pipeline
from ml_strategy.schemas import validate_bundle, validate_latest


def test_pipeline_writes_atomic_valid_snapshots(market_repo):
    master_path = market_repo / "data" / "security_master.json"
    master = json.loads(master_path.read_text(encoding="utf-8"))
    sectors = ("Финансы (Банки)", "Нефть и газ", "Металлы и добыча")
    for index, security in enumerate(master["securities"]):
        security["sector"] = sectors[index % len(sectors)]
    master_path.write_text(json.dumps(master), encoding="utf-8")
    config = StrategyConfig(
        min_training_rows=300,
        evaluation_folds=5,
        max_universe=18,
        min_cross_section=10,
    )
    bundle = run_pipeline(market_repo, config=config, today=date(2024, 12, 1))
    assert bundle["latest.json"]["portfolio"]["positions"]
    positions = bundle["latest.json"]["portfolio"]["positions"]
    assert all(
        {"current_shares", "trade_shares", "trade_value_rub", "trade_action"} <= row.keys()
        for row in positions
    )
    sector_position = positions[0]
    assert sector_position["sector_feature_pack"]
    assert sector_position["sector_feature_pack"]["included_in_model"] == (
        sector_position["sector_feature_pack"]["status"] == "APPROVED"
    )
    assert sector_position["sector_drivers"]
    assert all(
        driver["status"] == sector_position["sector_feature_pack"]["status"]
        for driver in sector_position["sector_drivers"]
    )
    assert bundle["latest.json"]["benchmark"] == "MCFTR"
    diagnostics = bundle["latest.json"]["portfolio"]["diagnostics"]
    assert diagnostics["positions_count"] == len(positions)
    assert diagnostics["top5_weight"] <= diagnostics["top5_limit"] + 0.01
    assert diagnostics["largest_sector_weight"] <= diagnostics["sector_limit"] + 0.01
    assert diagnostics["effective_positions"] > 0
    assert diagnostics["cash"]["is_market_timing_signal"] is False
    execution = bundle["latest.json"]["execution_policy"]
    assert execution["auto_execution_allowed"] is False
    assert execution["uses_user_holdings"] is False
    assert execution["comparison_basis"] == "previous_published_model_snapshot"
    assert execution["status"] == "MODEL_PORTFOLIO_READY"
    assert execution["model_portfolio_ready"] is True
    assert execution["manual_rebalance_plan_available"] is True
    assert bundle["latest.json"]["model"]["update_policy"]["specification_locked"] is True
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


def test_public_snapshot_rejects_unsafe_execution_claims_and_inconsistent_diagnostics(
    market_repo,
):
    config = StrategyConfig(
        min_training_rows=300,
        evaluation_folds=5,
        max_universe=18,
        min_cross_section=10,
    )
    payload = run_pipeline(
        market_repo,
        config=config,
        today=date(2024, 12, 1),
    )["latest.json"]
    unsafe = json.loads(json.dumps(payload))
    unsafe["execution_policy"]["auto_execution_allowed"] = True
    assert "latest: automatic execution must be disabled" in validate_latest(unsafe)

    readiness_mismatch = json.loads(json.dumps(payload))
    readiness_mismatch["execution_policy"]["manual_rebalance_plan_available"] = False
    assert "latest: manual rebalance availability mismatch" in validate_latest(
        readiness_mismatch
    )

    inconsistent = json.loads(json.dumps(payload))
    inconsistent["portfolio"]["diagnostics"]["top5_weight"] = 0
    assert "latest: diagnostics top5_weight mismatch" in validate_latest(inconsistent)
