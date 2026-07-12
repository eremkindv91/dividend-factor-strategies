from __future__ import annotations

import math

import pytest

from src.strategies.quality import (
    PortfolioConfig,
    build_quality_portfolio,
    compute_quality_scores,
    cross_section_zscores,
    deterministic_percentile_bounds,
    earnings_variability,
    enforce_caps,
    eps_growth,
    infer_issuer_id,
    percentile_rank,
    piecewise_quality_score,
    winsorize,
)


def test_winsorization_uses_deterministic_5_95_order_statistics():
    values = list(range(1, 201))
    out, bounds = winsorize(values)
    assert bounds == (10.0, 191.0)
    assert out[0] == 10.0
    assert out[-1] == 191.0


def test_winsorization_excludes_missing_values():
    out, bounds = winsorize([None, 1.0, 2.0, None, 100.0])
    assert out[0] is None and out[3] is None
    assert bounds == deterministic_percentile_bounds([1.0, 2.0, 100.0])


def test_identical_cross_section_returns_missing_zscores():
    zscores, stats = cross_section_zscores([5.0, 5.0, None, 5.0])
    assert zscores == [None, None, None, None]
    assert stats == {"mean": 5.0, "std": 0.0}


def test_debt_to_equity_zscore_is_inverted():
    zscores, _ = cross_section_zscores([1.0, 2.0, 3.0], inverse=True)
    assert zscores[0] > zscores[2]


def test_earnings_variability_zscore_is_inverted():
    zscores, _ = cross_section_zscores([0.1, 0.2, 0.9], inverse=True)
    assert zscores[0] > zscores[2]


def test_eps_growth_with_negative_previous_value_follows_sign_rule():
    assert eps_growth(-5.0, -10.0) == pytest.approx(0.5)
    assert eps_growth(5.0, -10.0) == pytest.approx(1.5)


def test_eps_growth_with_zero_previous_value_is_missing():
    assert eps_growth(5.0, 0.0) is None


def test_earnings_variability_requires_five_comparable_eps():
    assert earnings_variability([1, 2, 3, 4]) is None
    assert earnings_variability([1, 2, None, 4, 5]) is None
    assert earnings_variability([1, 2, 3, 4, 5]) == pytest.approx(0.335927, rel=1e-5)


def test_piecewise_quality_transform_is_positive_and_continuous():
    assert piecewise_quality_score(0) == 1
    assert piecewise_quality_score(2) == 3
    assert piecewise_quality_score(-2) == pytest.approx(1 / 3)


def test_percentile_rank_is_tie_aware():
    assert percentile_rank([1, 2, 2, 4], 2) == pytest.approx(50.0)


def _score_row(ticker, sector, roe, debt, evar=None, confidence="medium"):
    return {
        "ticker": ticker,
        "issuer_id": f"ticker:{ticker}",
        "sector": sector,
        "super_sector": "All",
        "raw": {"roe": roe, "debt_to_equity": debt, "earnings_variability": evar},
        "confidence": confidence,
        "financial_model_required": False,
        "investability": {"eligible": True, "reasons": []},
        "warnings": [],
        "exclusion_reasons": [],
    }


def test_roe_is_required_for_composite_score():
    rows = [_score_row("A", "S", None, 1, 0.2), _score_row("B", "S", 0.2, 2, 0.3)]
    scored, _ = compute_quality_scores(rows, {"winsorization": [0.05, 0.95], "min_quality_coverage": 0.67, "min_sector_peers": 2})
    assert scored[0]["score_eligible"] is False
    assert "missing_required_roe" in scored[0]["exclusion_reasons"]


def test_two_descriptor_score_is_allowed_at_coverage_067():
    rows = [_score_row(str(i), "S", 0.1 + i / 100, 1 + i / 10) for i in range(5)]
    scored, _ = compute_quality_scores(rows, {"winsorization": [0.05, 0.95], "min_quality_coverage": 0.67, "min_sector_peers": 5})
    assert all(row["score_eligible"] for row in scored)
    assert all(row["coverage_ratio"] == 0.67 for row in scored)


def test_sector_standardization_happens_after_absolute_composite():
    rows = [_score_row(f"A{i}", "A", 0.1 + i / 100, 1 + i / 10, 0.1 + i / 100) for i in range(5)]
    rows += [_score_row(f"B{i}", "B", 0.4 + i / 100, 2 + i / 10, 0.2 + i / 100) for i in range(5)]
    scored, _ = compute_quality_scores(rows, {"winsorization": [0.05, 0.95], "min_quality_coverage": 0.67, "min_sector_peers": 5})
    assert all(row["normalization_scope"] == "sector" for row in scored)
    assert all(row["quality_z_absolute"] is not None for row in scored)


def test_small_sector_uses_explicit_super_sector_fallback():
    rows = [_score_row(f"A{i}", "Small", 0.1 + i / 100, 1 + i / 10) for i in range(3)]
    rows += [_score_row(f"B{i}", "Large", 0.2 + i / 100, 1.5 + i / 10) for i in range(4)]
    scored, _ = compute_quality_scores(rows, {"winsorization": [0.05, 0.95], "min_quality_coverage": 0.67, "min_sector_peers": 5})
    small = [row for row in scored if row["sector"] == "Small"]
    assert all(row["normalization_scope"] == "super_sector" for row in small)
    assert all("sector_peer_fallback:super_sector" in row["warnings"] for row in small)


def test_sector_zscore_is_clipped_to_plus_minus_three():
    rows = [_score_row(f"A{i}", "A", 0.1, 1.0) for i in range(5)]
    rows.append(_score_row("OUT", "A", 20.0, 0.0))
    scored, _ = compute_quality_scores(rows, {"winsorization": [0.0, 1.0], "min_quality_coverage": 0.67, "min_sector_peers": 5})
    assert all(row["quality_z_sector"] is None or abs(row["quality_z_sector"]) <= 3 for row in scored)


def test_dual_share_classes_share_issuer_id():
    assert infer_issuer_id("SBER") == infer_issuer_id("SBERP")
    assert infer_issuer_id("TATN") == infer_issuer_id("TATNP")


def _portfolio_rows(n=10):
    rows = []
    for i in range(n):
        rows.append({
            "ticker": f"T{i}", "issuer_id": f"I{i}", "sector": "A" if i < n // 2 else "B",
            "score_eligible": True, "investable": True, "financial_model_required": False,
            "coverage_ratio": 1.0, "confidence": "medium", "market_cap_rub": 10e9 + i * 1e9,
            "free_float_market_cap_rub": 5e9 + i * 1e9, "adv_rub": 20e6 + i * 1e6,
            "quality_score_sector": 1 + i / 10, "quality_score_absolute": 1 + i / 20,
            "sector_rank_pct": 50 + i, "quality_rank_pct": 40 + i, "volatility": 0.2 + i / 100,
            "price": 100 + i, "lot_size": 10,
        })
    return rows


def test_issuer_cap_is_respected():
    rows = _portfolio_rows(10)
    rows[0]["issuer_id"] = rows[1]["issuer_id"] = "SAME"
    weights = enforce_caps(rows, [10] + [1] * 9, max_security_weight=0.2, max_issuer_weight=0.25, max_sector_weight=0.6)
    issuer_weight = sum(weight for row, weight in zip(rows, weights) if row["issuer_id"] == "SAME")
    assert issuer_weight <= 0.25 + 1e-8


def test_sector_cap_is_respected_after_redistribution():
    rows = _portfolio_rows(10)
    weights = enforce_caps(rows, [10 if row["sector"] == "A" else 1 for row in rows], max_security_weight=0.25, max_issuer_weight=0.3, max_sector_weight=0.55)
    assert sum(weight for row, weight in zip(rows, weights) if row["sector"] == "A") <= 0.55 + 1e-8


def test_cap_redistribution_keeps_weights_at_one():
    rows = _portfolio_rows(10)
    weights = enforce_caps(rows, [100] + [1] * 9, max_security_weight=0.2, max_issuer_weight=0.25, max_sector_weight=0.6)
    assert math.fsum(weights) == pytest.approx(1.0, abs=1e-8)


def test_infeasible_security_cap_fails_instead_of_silent_renormalization():
    with pytest.raises(ValueError, match="infeasible"):
        enforce_caps(_portfolio_rows(3), [1, 1, 1], max_security_weight=0.2, max_issuer_weight=0.5, max_sector_weight=0.8)


def test_lot_allocation_rounds_down_and_preserves_cash():
    result = build_quality_portfolio(
        _portfolio_rows(10),
        PortfolioConfig(top_n=10, weighting="equal", max_security_weight=0.2, max_issuer_weight=0.25,
                        max_sector_weight=0.6, capital=100_000),
    )
    assert result["target_weight_sum"] == pytest.approx(1.0)
    assert result["cash_rub"] >= 0
    assert all(item["quantity"] % item["lot_size"] == 0 for item in result["items"])


def test_low_confidence_is_excluded_by_default_and_requires_opt_in():
    rows = _portfolio_rows(10)
    for row in rows:
        row["confidence"] = "low"
    strict = build_quality_portfolio(rows, PortfolioConfig(top_n=10))
    preview = build_quality_portfolio(rows, PortfolioConfig(top_n=10, allow_low_confidence=True, max_sector_weight=0.6))
    assert strict["items"] == []
    assert len(preview["items"]) == 10


def test_one_security_per_issuer_keeps_more_liquid_class():
    rows = _portfolio_rows(10)
    rows[0]["issuer_id"] = rows[1]["issuer_id"] = "PAIR"
    rows[0]["adv_rub"] = 20e6
    rows[1]["adv_rub"] = 100e6
    result = build_quality_portfolio(rows, PortfolioConfig(top_n=9, max_security_weight=0.2, max_sector_weight=0.7))
    tickers = {item["ticker"] for item in result["items"]}
    assert "T1" in tickers
    assert "T0" not in tickers


def test_rank_buffer_keeps_existing_name_within_120_percent_band():
    rows = _portfolio_rows(10)
    result = build_quality_portfolio(
        rows,
        PortfolioConfig(top_n=5, max_security_weight=0.25, max_sector_weight=1.0,
                        buffer_enabled=True, previous_constituents=("T5",)),
    )
    assert "T5" in {item["ticker"] for item in result["items"]}
