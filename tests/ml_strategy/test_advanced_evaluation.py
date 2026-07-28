from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from ml_strategy.advanced_evaluation import (
    AdvancedExecutionError,
    _common_rows,
    _validate_execution,
    build_public_advanced_models,
)
from ml_strategy.schemas import validate_advanced_models, validate_public_advanced_models


def _predictions(model: str, dates=("2025-01-10", "2025-02-10")) -> pd.DataFrame:
    rows = []
    for date in dates:
        for index, ticker in enumerate(("AAA", "BBB", "CCC")):
            rows.append(
                {
                    "date": pd.Timestamp(date),
                    "ticker": ticker,
                    "model": model,
                    "forecast": 0.01 * (index + 1),
                    "actual": 0.02 * (index + 1),
                    "forward_total_return": 0.03 * (index + 1),
                }
            )
    return pd.DataFrame(rows)


def _metadata(**overrides) -> dict:
    metadata = {
        "execution_mode": "production_evaluation",
        "trained": True,
        "mock_backend": False,
        "checkpoint_exists": True,
        "folds": 2,
        "prediction_count": 6,
    }
    metadata.update(overrides)
    return metadata


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"trained": False}, "trained"),
        ({"mock_backend": True}, "mock_backend"),
        ({"checkpoint_exists": False}, "checkpoint"),
    ],
)
def test_execution_integrity_rejects_nonproduction_evidence(override, reason):
    with pytest.raises(AdvancedExecutionError, match=reason):
        _validate_execution("candidate", _metadata(**override), _predictions("candidate"))


def test_execution_integrity_rejects_empty_predictions():
    with pytest.raises(AdvancedExecutionError, match="oos_predictions"):
        _validate_execution(
            "candidate",
            _metadata(prediction_count=0),
            _predictions("candidate").iloc[0:0],
        )


def test_common_window_checks_identical_targets():
    baseline = _predictions("elastic_net")
    candidate = _predictions("patchtst", dates=("2025-02-10", "2025-03-10"))
    evaluations = {
        "elastic_net": SimpleNamespace(predictions=baseline, champion="elastic_net"),
        "patchtst": SimpleNamespace(predictions=candidate, champion="patchtst"),
    }
    common, metadata = _common_rows(evaluations)
    assert metadata["rows"] == 3
    assert metadata["identical_rows"] is True
    assert metadata["identical_targets"] is True
    assert metadata["models"]["elastic_net"]["coverage"] == 0.5
    assert len(common["patchtst"]) == 3


def test_advanced_schema_rejects_mock_execution():
    models = {}
    for name in ("elastic_net", "elastic_net_iceemdan", "patchtst"):
        models[name] = {
            "status": "PRODUCTION_CHAMPION" if name == "elastic_net" else "EVALUATED_REJECTED",
            "execution": {
                "execution_mode": "production_evaluation",
                "trained": True,
                "mock_backend": name == "patchtst",
                "backend": "test",
                "checkpoint": "checkpoint",
                "checkpoint_exists": True,
                "folds": 2,
                "prediction_count": 6,
                "common_test_window": {"rows": 6},
            },
        }
    payload = {
        "execution_mode": "production_evaluation",
        "models": models,
        "common_test_window": {
            "rows": 6,
            "identical_rows": True,
            "identical_targets": True,
        },
        "comparison_integrity": {
            "same_universe_rows": True,
            "same_targets": True,
            "same_rebalance_dates": True,
            "same_transaction_costs": True,
            "same_optimizer_constraints": True,
        },
    }
    assert "advanced_models: patchtst mock backend" in validate_advanced_models(payload)


def _full_evaluation_payload() -> dict:
    models = {}
    values = {
        "elastic_net": ("ElasticNet", "PRODUCTION_CHAMPION", -0.0045, 0.08, -0.2564),
        "elastic_net_iceemdan": (
            "ElasticNet + ICEEMDAN features",
            "EVALUATED_REJECTED",
            -0.0151,
            0.03,
            -0.2714,
        ),
        "patchtst": ("PatchTST", "EVALUATED_REJECTED", -0.0538, -0.22, -0.2195),
    }
    for model_id, (label, status, cagr, sharpe, drawdown) in values.items():
        models[model_id] = {
            "label": label,
            "status": status,
            "execution": {"trained": True},
            "common_window": {
                "prediction": {
                    "rank_ic": 0.123,
                    "pearson_ic": 0.107,
                    "rank_icir": 2.01,
                    "hit_rate": 0.52,
                    "top_bottom_spread": 0.01 if model_id != "patchtst" else -0.006,
                    "oos_rows": 1440,
                    "folds": 24,
                },
                "portfolio": {
                    "cagr_net": cagr,
                    "sharpe_net": sharpe,
                    "max_drawdown_net": drawdown,
                    "average_turnover": 0.4,
                    "total_cost_return": 0.0288,
                },
            },
        }
        if model_id != "elastic_net":
            models[model_id]["promotion"] = {
                "approved": False,
                "forecast_checks": {"rank_ic_improvement": False},
                "portfolio_checks": {"better_after_cost_excess": False},
            }
    return {
        "schema_version": 1,
        "generated_at": "2026-07-28T08:41:28+00:00",
        "production_model_unchanged": True,
        "common_test_window": {
            "start": "2024-08-09",
            "end": "2026-06-02",
            "rebalance_dates": 24,
            "rows": 1440,
        },
        "comparison_integrity": {
            "same_universe_rows": True,
            "same_targets": True,
            "same_rebalance_dates": True,
            "same_transaction_costs": True,
            "same_optimizer_constraints": True,
            "transaction_cost_bps_one_way": 30.0,
            "optimizer_constraints": {"turnover_cap": 0.4},
        },
        "models": models,
    }


def test_public_artifact_separates_production_and_research_roles():
    public = build_public_advanced_models(_full_evaluation_payload(), {"packs": []})
    assert validate_public_advanced_models(public) == []
    production = next(row for row in public["models"] if row["role"] == "production")
    challengers = [row for row in public["models"] if row["role"] == "research_challenger"]
    assert production["model"] == "ElasticNet"
    assert production["affects_current_portfolio"] is True
    assert all(row["affects_current_portfolio"] is False for row in challengers)
    assert public["sector_packs"]["affects_current_portfolio"] is False
    assert (
        public["production_governance"]["absolute_performance_assessment"]["status"]
        == "WEAK_NEEDS_IMPROVEMENT"
    )


@pytest.mark.parametrize(
    "unsafe_value",
    [
        "/tmp/private/checkpoint.pt",
        "/Users/tester/model.pt",
        "ghp_abcdefghijklmnopqrstuvwxyz1234567890",
        "api_key=not-a-real-key",
    ],
)
def test_public_artifact_rejects_machine_paths_and_secret_patterns(unsafe_value):
    public = build_public_advanced_models(_full_evaluation_payload(), {"packs": []})
    public["unsafe_test_value"] = unsafe_value
    assert validate_public_advanced_models(public)


def test_public_artifact_rejects_checkpoint_metadata():
    public = build_public_advanced_models(_full_evaluation_payload(), {"packs": []})
    public["models"][0]["checkpoint"] = "model.pt"
    assert any(
        "forbidden key" in error for error in validate_public_advanced_models(public)
    )
