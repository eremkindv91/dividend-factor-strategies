from __future__ import annotations

from copy import deepcopy

import pytest

from ml_strategy.execution import (
    decide_strategy_state,
    extract_published_portfolio,
    public_candidate,
)


@pytest.mark.parametrize(
    ("kwargs", "signal", "action", "publish"),
    [
        ({"material_change": True}, "valid", "rebalance", True),
        ({"material_change": False}, "valid", "hold", False),
        ({"predictive_gate_passed": False}, "rejected", "no_trade", False),
        ({"portfolio_gate_passed": False}, "rejected", "no_trade", False),
        ({"model_status": "research_only"}, "rejected", "no_trade", False),
        ({"data_status": "stale"}, "stale", "frozen", False),
        ({"data_status": "degraded"}, "no_signal", "frozen", False),
        ({"solver_succeeded": False}, "solver_failed", "frozen", False),
    ],
)
def test_state_machine_with_previous_portfolio(kwargs, signal, action, publish):
    values = {
        "model_status": "production",
        "data_status": "pass",
        "predictive_gate_passed": True,
        "portfolio_gate_passed": True,
        "solver_succeeded": True,
        "has_published_portfolio": True,
        "material_change": True,
    }
    values.update(kwargs)
    decision = decide_strategy_state(**values)
    assert decision.signal_status == signal
    assert decision.action_status == action
    assert decision.publish_candidate is publish


def test_degraded_without_previous_portfolio_has_no_target():
    decision = decide_strategy_state(
        model_status="production",
        data_status="degraded",
        predictive_gate_passed=True,
        portfolio_gate_passed=True,
        solver_succeeded=True,
        has_published_portfolio=False,
        material_change=True,
    )
    assert decision.action_status == "no_trade"
    assert decision.title == "Целевой состав не сформирован"
    assert not decision.publish_candidate


def test_rejected_candidate_has_no_executable_fields_and_does_not_mutate_input():
    source = {
        "ticker": "SBER",
        "name": "Сбербанк",
        "sector": "Финансы",
        "target_weight": 0.15,
        "shares": 500,
        "target_rub": 150_000,
        "trade_rub": 25_000,
        "change_weight": 0.025,
        "expected_excess_return_20d": 0.012,
    }
    before = deepcopy(source)
    candidate = public_candidate(source)
    assert candidate["calculated_weight"] == 0.15
    assert not {"shares", "target_rub", "trade_rub", "change_weight", "target_weight"}.intersection(candidate)
    assert source == before


def test_only_explicitly_published_or_actionable_legacy_portfolio_is_reused():
    published = {"published_from_run_id": "run-1", "positions": [{"ticker": "SBER", "target_weight": 1.0}]}
    assert extract_published_portfolio({"published_portfolio": published}) == published
    assert extract_published_portfolio({"portfolio": published, "model": {"status": "RESEARCH_ONLY"}}) is None
    legacy = {"portfolio": published, "model": {"status": "APPROVED"}, "signal": {"action": "NO_ACTION"}}
    assert extract_published_portfolio(legacy) == published
