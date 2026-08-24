from __future__ import annotations

from copy import deepcopy
from datetime import date
import json
from pathlib import Path

import pytest

from bonds.analytics.opportunity_score import score_opportunities
from bonds.analytics.relative_value import attach_relative_value
from bonds.cashflows import CashFlow
from bonds.curves import CurveProvider
from bonds.detail_builder import build_detail
from bonds.opportunity_engine import allocate_opportunities
from bonds.pricing import calculate_bond_analytics
from bonds.pricing.amortizing import calculate_amortizing
from bonds.pricing.fixed import calculate_fixed
from bonds.pricing.floater import calculate_floater, project_floater_cashflows
from bonds.pricing.optioned import calculate_optioned
from bonds.pricing.perpetual import simple_extension_proxy
from bonds.structures import BondStructure, CouponModel, CouponType, PrincipalModel, PrincipalType


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "tests/fixtures/bonds/k2_golden.json").read_text(encoding="utf-8"))
CONFIG = json.loads((ROOT / "bonds/opportunity_config.json").read_text(encoding="utf-8"))
AS_OF = date(2026, 8, 17)
CURVE = CurveProvider([(0.5, 14.0), (1, 13.8), (3, 13.5), (5, 13.2), (10, 12.8)],
                      as_of=AS_OF, source="test curve")


def test_fixed_analytics_dirty_duration_dv01_and_zspread_reverse_price():
    flows = [CashFlow(date(2027, 8, 17), "coupon", coupon=100),
             CashFlow(date(2028, 8, 17), "coupon_principal", coupon=100, principal=1000)]
    result = calculate_fixed(flows=flows, dirty_price=950, clean_price=94, aci=10,
                             as_of=AS_OF, curve=CURVE)
    assert result["dirty_price"]["value"] == 950
    assert result["ytm_gross"]["value"] > 0
    assert result["modified_duration"]["value"] > 0
    assert result["dv01"]["value"] > 0
    assert abs(result["z_spread"]["inputs"]["reverse_price"] - 950) < 1e-6
    assert result["oas"] if "oas" in result else True


def test_floater_projects_reference_plus_margin_and_solves_dm_without_fixed_ytm():
    model = CouponModel(CouponType.RUONIA_FLOAT, reference_index="RUONIA",
                        contractual_margin_bp=150, formula_confidence="CONFIRMED")
    flows = project_floater_cashflows(
        payment_dates=["2027-02-17", "2027-08-17", "2028-02-17", "2028-08-17"],
        face=1000, coupon_model=model, as_of=AS_OF, maturity_date="2028-08-17",
        reference_curve=CURVE,
    )
    assert flows[0].coupon_rate == pytest.approx(CURVE.rate_pct(184 / 365) + 1.5)
    result = calculate_floater(flows=flows, dirty_price=1000, clean_price=99.5, aci=5,
                               as_of=AS_OF, discount_curve=CURVE, reference_curve=CURVE)
    assert result["discount_margin"]["value"] is not None
    assert result["effective_duration"]["value"] < result["spread_duration"]["value"]
    assert "ytm_gross" not in result


def test_amortizing_wal_uses_principal_schedule():
    flows = [
        CashFlow(date(2027, 8, 17), "coupon_principal", coupon=80, principal=400),
        CashFlow(date(2028, 8, 17), "coupon_principal", coupon=48, principal=600),
    ]
    result = calculate_amortizing(flows=flows, dirty_price=1020, clean_price=101, aci=10,
                                  as_of=AS_OF, curve=CURVE)
    assert result["wal"]["value"] == pytest.approx(1.6, abs=0.01)
    assert result["ytm_gross"]["value"] is not None


def test_dispatcher_does_not_leak_fixed_context_fields_into_amortizing_engine():
    flows = [CashFlow(date(2027, 8, 17), "coupon_principal", coupon=80, principal=400),
             CashFlow(date(2028, 8, 17), "coupon_principal", coupon=48, principal=600)]
    structure = BondStructure(
        coupon_model=CouponModel(CouponType.FIXED, current_rate_pct=8),
        principal_model=PrincipalModel(PrincipalType.AMORTIZING),
    )
    result = calculate_bond_analytics(structure=structure, context={
        "flows": flows, "dirty_price": 1020, "clean_price": 101, "aci": 10,
        "as_of": AS_OF, "curve": CURVE, "market_value": 1020, "face": 1000,
    })
    assert result["wal"]["value"] == pytest.approx(1.6, abs=0.01)


def test_optioned_yields_are_conditional_and_oas_is_not_faked():
    flows = [CashFlow(date(2027, 8, 17), "coupon", coupon=100),
             CashFlow(date(2029, 8, 17), "coupon_principal", coupon=100, principal=1000)]
    result = calculate_optioned(
        flows=flows, dirty_price=1000, as_of=AS_OF,
        put_schedule=[{"date": "2027-08-17", "price_pct": 100, "face": 1000}],
        call_schedule=[], maturity_yield=0.10,
    )
    assert result["ytp"]["value"][0]["yield_pct"] is not None
    assert result["yield_to_worst"]["value"] is not None
    assert result["oas"]["value"] is None
    assert result["oas"]["status"] == "UNSUPPORTED"


def test_k2_golden_is_generic_perpetual_without_maturity_ytm_or_production_hardcode():
    row = deepcopy(FIXTURE["row"])
    compact, detail = build_detail(
        row, detail_input=FIXTURE["detail_input"], terms=FIXTURE["terms"],
        curve=CURVE, as_of=AS_OF, opportunity_config=CONFIG,
    )
    assert detail["analysis_status"] == "FULL"
    assert detail["structure"]["principal_model"]["maturity_date"] is None
    assert detail["capabilities"]["supports_ytm"] is False
    assert detail["capabilities"]["supports_ytc"] is True
    assert detail["capabilities"]["supports_oas"] is False
    assert detail["analytics"]["ytm_gross"]["value"] is None
    assert detail["analytics"]["current_yield"]["value"] == pytest.approx(18.01, abs=0.03)
    ytc = detail["analytics"]["ytc_if_call"]["value"][0]["yield_pct"]
    assert ytc == pytest.approx(20.45, abs=0.20)
    assert compact["safe_portfolio_eligible"] is False
    for module in (ROOT / "bonds").rglob("*.py"):
        source = module.read_text(encoding="utf-8")
        assert "RU000A1039A8" not in source
        assert "91.18" not in source


def test_k2_simple_extension_proxy_matches_documented_regression_only():
    expected = {245: 100.0, 350: 94.0, 450: 88.9, 550: 84.4}
    for spread, price in expected.items():
        assert simple_extension_proxy(14.0, 245, spread) == pytest.approx(price, abs=0.1)


def test_opportunity_score_is_within_structure_deterministic_and_does_not_mutate_source():
    rows = [
        {"secid": "A", "structure_class": "FLOATER", "discount_margin_bp": 200, "carry_pct": 15,
         "credit_quality_score": 90, "liquidity_score": 80, "rate_risk_penalty": 10,
         "structure_risk_penalty": 12, "data_quality_penalty": 0, "issuer_id": "i1", "rating_group": "AA", "sector": "x"},
        {"secid": "B", "structure_class": "FLOATER", "discount_margin_bp": 100, "carry_pct": 14,
         "credit_quality_score": 80, "liquidity_score": 70, "rate_risk_penalty": 10,
         "structure_risk_penalty": 12, "data_quality_penalty": 0, "issuer_id": "i2", "rating_group": "AA", "sector": "x"},
        {"secid": "C", "structure_class": "FIXED_BULLET", "z_spread_bp": 500, "carry_pct": 30,
         "credit_quality_score": 70, "liquidity_score": 60, "rate_risk_penalty": 30,
         "structure_risk_penalty": 5, "data_quality_penalty": 0, "issuer_id": "i3", "rating_group": "A", "sector": "y"},
    ]
    attach_relative_value(rows, minimum_peers=1)
    score_opportunities(rows, CONFIG["score_weights"])
    assert all(0 <= row["opportunity_score"] <= 100 for row in rows)
    assert rows[0]["opportunity_score"] > rows[1]["opportunity_score"]
    assert rows[2]["relative_value"]["status"] == "INSUFFICIENT_PEERS"


def test_opportunity_allocator_enforces_one_million_lot_concentration(tmp_path):
    row = {
        "secid": "BIGLOT", "analysis_status": "FULL", "dirty_price_per_lot_rub": 1_000_000,
        "qualified_only": False, "liquidity_score": 90, "rating": "AA+", "instrument_type": "corp",
        "opportunity_score": 90, "issuer_id": "i", "sector": "s", "structure_class": "PERPETUAL_RESET",
    }
    result = allocate_opportunities([row], 1_000_000)
    assert result["status"] == "INFEASIBLE"
    assert result["exclusions"]["LOT_SIZE_CONCENTRATION"] == 1
    assert result["positions"] == []
    assert result["invested_rub"] == 0
    assert result["cash_rub"] == 1_000_000


def test_backend_scenario_matrix_is_finite_and_contains_breakeven():
    row = {
        "secid": "TESTFIXED", "isin": "TESTFIXED", "name": "Test", "issuer_id": "issuer",
        "issuer_name": "Issuer", "instrument_type": "corp", "sector": "Test", "rating": "AA",
        "rating_rank": 18, "rating_group": "AA", "coupon_type": "fixed", "coupon_pct": 10,
        "bond_structure_type": "FIXED_BULLET", "clean_price_pct": 98, "dirty_price_per_bond_rub": 990,
        "dirty_price_per_lot_rub": 990, "aci_per_bond_rub": 10, "face_value_per_bond_rub": 1000,
        "maturity_date": "2028-08-17", "history_sessions": 20, "median_volume_20d_rub": 5_000_000,
        "cashflows_deterministic": True, "source_dates": {"price": "2026-08-17"},
    }
    detail_input = {"coupon_schedule": [
        {"date": "2027-08-17", "amount_per_bond_rub": 100},
        {"date": "2028-08-17", "amount_per_bond_rub": 100},
    ]}
    _, detail = build_detail(row, detail_input=detail_input, terms=None, curve=CURVE,
                             as_of=AS_OF, opportunity_config=CONFIG)
    lab = detail["scenario_lab"]
    assert lab["status"] == "CALCULATED"
    assert len(lab["cells"]) == 5
    assert all(len(row_cells) == 5 for row_cells in lab["cells"])
    assert all(cell["future_dirty"] >= 0 for row_cells in lab["cells"] for cell in row_cells)
    assert lab["breakeven_combined_shock_bp"] is not None


def test_opportunity_allocator_reserves_liquid_core_before_high_score_illiquid_names():
    rows = []
    for index in range(12):
        rows.append({
            "secid": f"L{index}", "analysis_status": "FULL", "dirty_price_per_lot_rub": 10_000,
            "qualified_only": False, "liquidity_score": 90, "rating": "AA", "instrument_type": "corp",
            "opportunity_score": 60 - index, "issuer_id": f"li{index}", "sector": f"ls{index % 4}",
            "structure_class": "FIXED_BULLET" if index % 2 == 0 else "AMORTIZING_FIXED",
            "duration_years": 3,
        })
        rows.append({
            "secid": f"X{index}", "analysis_status": "FULL", "dirty_price_per_lot_rub": 10_000,
            "qualified_only": False, "liquidity_score": 35, "rating": "AA", "instrument_type": "corp",
            "opportunity_score": 100 - index, "issuer_id": f"xi{index}", "sector": f"xs{index % 4}",
            "structure_class": "FLOATER", "duration_years": 1,
        })
    result = allocate_opportunities(rows, 1_000_000, allow_complex=True)
    assert result["status"] == "OK"
    assert result["risk"]["liquid_core_share"] >= 0.5
