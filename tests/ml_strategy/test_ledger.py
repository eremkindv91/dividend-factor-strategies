from __future__ import annotations

import json
from datetime import date

import pytest

from ml_strategy.config import StrategyConfig
from ml_strategy.ledger import prepare_ledger, validate_ledger
from ml_strategy.pipeline import run_pipeline
from ml_strategy.schemas import publish_bundle


def _config():
    return StrategyConfig(
        min_training_rows=300,
        evaluation_folds=4,
        max_universe=18,
        min_cross_section=10,
    )


def test_pipeline_creates_append_only_hash_chain_without_duplicates(market_repo):
    first = run_pipeline(market_repo, config=_config(), today=date(2024, 12, 1))
    index = first["ledger/index.json"]
    assert len(index["records"]) == len(first["latest.json"]["portfolio"]["positions"])
    assert not validate_ledger(index)
    assert all(1 <= row["forecast_rank_bucket"] <= 10 for row in index["records"])
    chain_head = index["chain_head"]
    second = run_pipeline(market_repo, config=_config(), today=date(2024, 12, 1))
    assert len(second["ledger/index.json"]["records"]) == len(index["records"])
    assert second["ledger/index.json"]["chain_head"] == chain_head
    assert len(second["ledger/open/2024-11-29.json"]["records"]) == len(index["records"])
    first_positions = {
        row["ticker"]: row
        for row in first["latest.json"]["portfolio"]["positions"]
    }
    assert all(
        row["current_shares"] == first_positions[row["ticker"]]["current_shares"]
        and row["trade_action"] == first_positions[row["ticker"]]["trade_action"]
        and row["target_weight"] == first_positions[row["ticker"]]["target_weight"]
        for row in second["latest.json"]["portfolio"]["positions"]
    )
    assert second["latest.json"]["live_track_record"]["mode"] == "SHADOW_LIVE"


def test_changed_forecast_for_same_cutoff_is_rejected(market_repo):
    bundle = run_pipeline(market_repo, config=_config(), today=date(2024, 12, 1))
    latest = json.loads(json.dumps(bundle["latest.json"]))
    latest["portfolio"]["positions"][0]["expected_excess_return_20d"] += 0.01
    with pytest.raises(ValueError, match="immutable forecast conflict"):
        prepare_ledger(market_repo, latest, bundle["model_card.json"], _config())


def test_mature_forecast_resolves_from_real_cached_prices(market_repo):
    dates = json.loads((market_repo / "data" / "security_master.json").read_text())
    assert dates["securities"]
    latest = {
        "generated_at": "2023-03-01T18:00:00+00:00",
        "data_as_of": "2022-12-30",
        "benchmark": "MCFTR",
        "horizon_sessions": 20,
        "portfolio": {
            "positions": [
                {
                    "ticker": "T00",
                    "price_rub": 60.0,
                    "expected_excess_return_20d": 0.01,
                    "target_weight": 0.1,
                    "change_weight": 0.1,
                }
            ]
        },
    }
    files, metrics = prepare_ledger(
        market_repo,
        latest,
        {"features": ["return_20d"]},
        _config(),
    )
    index = files["ledger/index.json"]
    assert len(index["records"]) == 1
    assert len(index["resolutions"]) == 1
    assert metrics["resolved_forecasts"] == 1
    assert index["resolutions"][0]["rank_bucket"] == 1
    assert index["resolutions"][0]["resolution_method"].startswith("official_MOEX")


def test_invalid_ledger_does_not_replace_last_valid_snapshot(market_repo):
    bundle = run_pipeline(market_repo, config=_config(), today=date(2024, 12, 1))
    ledger_path = market_repo / "data" / "ml_strategy" / "ledger" / "index.json"
    latest_path = market_repo / "data" / "ml_strategy" / "latest.json"
    previous_ledger = ledger_path.read_text(encoding="utf-8")
    previous_latest = latest_path.read_text(encoding="utf-8")
    corrupted = json.loads(json.dumps(bundle))
    corrupted["ledger/index.json"]["records"][0]["point_forecast_excess_return"] += 0.01
    with pytest.raises(ValueError, match="ledger: invalid content hash"):
        publish_bundle(
            corrupted,
            data_root=market_repo / "data" / "ml_strategy",
            site_root=market_repo / "site" / "ml_strategy",
            history_date=bundle["latest.json"]["data_as_of"],
        )
    assert ledger_path.read_text(encoding="utf-8") == previous_ledger
    assert latest_path.read_text(encoding="utf-8") == previous_latest
