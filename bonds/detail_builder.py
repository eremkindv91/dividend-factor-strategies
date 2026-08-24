"""Build compact v4 universe rows and lazy structure-aware detail payloads."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import math
from typing import Any

from bonds.cashflows import CashFlow, aggregate_same_date
from bonds.curves import CurveProvider
from bonds.pricing import calculate_bond_analytics
from bonds.analytics.scenarios import breakeven_shock, total_return
from bonds.structures import (
    AnalysisStatus, BondStructure, CouponModel, CouponType, Optionality,
    PrincipalModel, PrincipalType, Seniority, analysis_status_for,
    capabilities_for, legacy_structure_type, structure_class,
)


def _date(value: str | None) -> date | None:
    try:
        return datetime.fromisoformat(str(value)[:10]).date() if value else None
    except ValueError:
        return None


def _float(value) -> float | None:
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _schedule(value) -> tuple[dict, ...]:
    return tuple(dict(item) for item in (value or []) if isinstance(item, dict))


def build_structure(row: dict, detail_input: dict | None = None, terms: dict | None = None) -> BondStructure:
    detail_input, terms = detail_input or {}, terms or {}
    declared = terms.get("structure") or {}
    coupon_terms = declared.get("coupon_model") or terms.get("coupon_model") or {}
    principal_terms = declared.get("principal_model") or terms.get("principal_model") or {}
    option_terms = declared.get("optionality") or terms.get("optionality") or {}
    legacy_coupon = str(row.get("coupon_type") or "").lower()
    legacy_structure = row.get("bond_structure_type")
    coupon_type = coupon_terms.get("type")
    if not coupon_type:
        coupon_type = {
            "fixed": CouponType.FIXED, "zero": CouponType.ZERO,
            "floating": CouponType.OTHER_FLOAT, "index_linked": CouponType.INDEX_LINKED,
        }.get(legacy_coupon, CouponType.INDEX_LINKED if row.get("index_linked") else CouponType.FIXED)
    principal_type = principal_terms.get("type")
    if not principal_type:
        principal_type = (
            PrincipalType.PERPETUAL if terms.get("perpetual")
            else PrincipalType.VARIABLE_NOMINAL if row.get("variable_nominal")
            else PrincipalType.AMORTIZING if row.get("amortizing")
            else PrincipalType.BULLET
        )
    put_schedule = _schedule(option_terms.get("put_schedule") or detail_input.get("offer_schedule"))
    call_schedule = _schedule(option_terms.get("call_schedule") or detail_input.get("call_schedule"))
    if row.get("has_call") and not call_schedule and row.get("call_option_date"):
        call_schedule = ({"date": row["call_option_date"], "price_pct": None, "source": "MOEX ISS"},)
    seniority_value = declared.get("seniority") or terms.get("seniority") or "UNKNOWN"
    try:
        seniority = Seniority(seniority_value)
    except ValueError:
        seniority = Seniority.UNKNOWN
    return BondStructure(
        coupon_model=CouponModel(
            type=CouponType(coupon_type),
            current_rate_pct=_float(coupon_terms.get("current_rate_pct")) if coupon_terms else _float(row.get("coupon_pct")),
            reference_index=coupon_terms.get("reference_index"),
            contractual_margin_bp=_float(coupon_terms.get("contractual_margin_bp")),
            floor_pct=_float(coupon_terms.get("floor_pct")), cap_pct=_float(coupon_terms.get("cap_pct")),
            reset_frequency=int(coupon_terms["reset_frequency"]) if coupon_terms.get("reset_frequency") else None,
            observation_lag_days=int(coupon_terms["observation_lag_days"]) if coupon_terms.get("observation_lag_days") is not None else None,
            next_reset_date=coupon_terms.get("next_reset_date"),
            formula_confidence=str(coupon_terms.get("formula_confidence") or "UNKNOWN"),
        ),
        principal_model=PrincipalModel(
            type=PrincipalType(principal_type),
            maturity_date=None if PrincipalType(principal_type) == PrincipalType.PERPETUAL else principal_terms.get("maturity_date") or row.get("maturity_date"),
            amortization_schedule=_schedule(principal_terms.get("amortization_schedule") or detail_input.get("amortization_schedule")),
        ),
        optionality=Optionality(
            has_put=bool(option_terms.get("has_put", row.get("has_put_offer"))) or bool(put_schedule),
            put_schedule=put_schedule,
            has_call=bool(option_terms.get("has_call", row.get("has_call"))) or bool(call_schedule),
            call_schedule=call_schedule,
        ),
        seniority=seniority,
        coupon_deferrable=declared.get("coupon_deferrable", terms.get("coupon_deferrable")),
        coupon_cumulative=declared.get("coupon_cumulative", terms.get("coupon_cumulative")),
        guarantee_status=declared.get("guarantee_status", terms.get("guarantee_status")),
        government_support_mechanism=declared.get("government_support_mechanism", terms.get("government_support_mechanism")),
        qualified_only=bool(row.get("qualified_only") or declared.get("qualified_only") or terms.get("qualified_only")),
        legal_flags=tuple(declared.get("legal_flags") or terms.get("legal_flags") or []),
    )


def build_cashflows(row: dict, detail_input: dict, structure: BondStructure, as_of: date) -> list[CashFlow]:
    flows: list[CashFlow] = []
    for item in detail_input.get("coupon_schedule") or []:
        dt, amount = _date(item.get("date")), _float(item.get("amount_per_bond_rub"))
        if dt and dt > as_of and amount is not None and amount >= 0:
            flows.append(CashFlow(dt, "coupon", coupon=amount, coupon_rate=_float(item.get("coupon_rate_pct")),
                                  source=str(item.get("source") or "MOEX ISS bondization"), model_flag="contractual"))
    for item in structure.principal_model.amortization_schedule:
        dt, amount = _date(item.get("date")), _float(item.get("amount_per_bond_rub"))
        if dt and dt > as_of and amount is not None and amount > 0:
            flows.append(CashFlow(dt, "principal", principal=amount,
                                  source=str(item.get("source") or "MOEX ISS bondization"), model_flag="contractual"))
    if structure.principal_model.type == PrincipalType.BULLET:
        maturity = _date(structure.principal_model.maturity_date)
        face = _float(row.get("face_value_per_bond_rub"))
        if maturity and maturity > as_of and face and not any(f.date == maturity and f.principal > 0 for f in flows):
            flows.append(CashFlow(maturity, "principal", principal=face, source="MOEX ISS", model_flag="contractual"))
    return aggregate_same_date(flows)


def _metric_value(analytics: dict, key: str):
    return (analytics.get(key) or {}).get("value")


def _scenario_lab(*, structure_class_name: str, analytics: dict, flows: list[CashFlow],
                  dirty_price: float | None, as_of: date) -> dict:
    """Build transparent backend sensitivity output; the browser only renders it."""
    duration = _metric_value(analytics, "effective_duration") or _metric_value(analytics, "modified_duration")
    convexity = _metric_value(analytics, "convexity") or 0.0
    if dirty_price is None or dirty_price <= 0 or duration is None:
        return {"status": "UNAVAILABLE", "reason": "DURATION_OR_PRICE_UNAVAILABLE"}
    horizon = as_of + timedelta(days=365)
    income = sum(flow.amount for flow in flows if as_of < flow.date <= horizon)

    def future_price(total_shock_bp: float) -> float:
        dy = total_shock_bp / 10000.0
        ratio = 1.0 - float(duration) * dy + 0.5 * float(convexity) * dy * dy
        return max(0.0, dirty_price * ratio)

    axis = [-300, -150, 0, 150, 300]
    cells = []
    for curve_bp in axis:
        row = []
        for spread_bp in axis:
            shock = curve_bp + spread_bp
            future = future_price(shock)
            gross = total_return(current_dirty=dirty_price, future_dirty=future, cashflows_received=income)
            row.append({
                "curve_bp": curve_bp, "spread_bp": spread_bp,
                "future_dirty": round(future, 6), "coupon_income": round(income, 6),
                "price_pnl": round(future - dirty_price, 6),
                "gross_total_return_pct": round(gross * 100, 6),
                "net_estimate_pct": round((gross - 0.003) * 100, 6),
            })
        cells.append(row)
    breakeven = breakeven_shock(
        future_price, current_dirty=dirty_price, cashflows_received=income, costs=dirty_price * 0.003,
        lower_bp=-5000, upper_bp=5000,
    )
    return {
        "status": "CALCULATED", "structure_class": structure_class_name,
        "method": "duration_convexity_one_year_sensitivity",
        "method_warning": "Локальная оценка чувствительности, не прогноз цены и не full revaluation.",
        "horizon_days": 365, "curve_shocks_bp": axis, "spread_shocks_bp": axis,
        "cells": cells, "breakeven_combined_shock_bp": breakeven,
        "assumed_costs_bp": 30,
    }


def build_detail(row: dict, *, detail_input: dict | None, terms: dict | None,
                 curve: CurveProvider | None, as_of: date, opportunity_config: dict) -> tuple[dict, dict]:
    detail_input = detail_input or {}
    structure = build_structure(row, detail_input, terms)
    flows = build_cashflows(row, detail_input, structure, as_of)
    coupon_schedule_complete = bool(detail_input.get("coupon_schedule")) or (
        structure.coupon_model.type == CouponType.ZERO
        or float(structure.coupon_model.current_rate_pct or 0.0) == 0.0
    )
    principal_schedule_complete = (
        bool(structure.principal_model.maturity_date)
        if structure.principal_model.type == PrincipalType.BULLET
        else bool(structure.principal_model.amortization_schedule)
        if structure.principal_model.type == PrincipalType.AMORTIZING
        else bool(structure.optionality.call_schedule)
        if structure.principal_model.type == PrincipalType.PERPETUAL
        else False
    )
    schedule_complete = bool(flows) and coupon_schedule_complete and principal_schedule_complete and all(
        item.get("price_pct") is not None for item in
        (*structure.optionality.put_schedule, *structure.optionality.call_schedule)
    )
    status = analysis_status_for(
        structure, has_price=_float(row.get("dirty_price_per_bond_rub")) is not None,
        has_schedule=schedule_complete,
    )
    capabilities = capabilities_for(structure, status)
    analytics: dict[str, Any] = {}
    warnings: list[str] = []
    if status == AnalysisStatus.FULL:
        context: dict[str, Any] = {
            "flows": flows,
            "dirty_price": float(row["dirty_price_per_bond_rub"]),
            "clean_price": float(row["clean_price_pct"]),
            "aci": float(row.get("aci_per_bond_rub") or 0.0),
            "as_of": as_of,
            "face": float(row["face_value_per_bond_rub"]),
            "curve": curve,
            "market_value": float(row["dirty_price_per_lot_rub"]),
        }
        if structure.principal_model.type == PrincipalType.PERPETUAL:
            annual_coupon = float(row["face_value_per_bond_rub"]) * float(structure.coupon_model.current_rate_pct or 0) / 100
            context = {
                "flows": flows, "clean_price_pct": float(row["clean_price_pct"]),
                "dirty_price": float(row["dirty_price_per_bond_rub"]), "face": float(row["face_value_per_bond_rub"]),
                "annual_coupon": annual_coupon, "as_of": as_of,
            }
        elif structure.coupon_model.type in {CouponType.RUONIA_FLOAT, CouponType.KEY_RATE_FLOAT, CouponType.KBD_FLOAT, CouponType.OTHER_FLOAT}:
            if curve is None:
                status = AnalysisStatus.PARTIAL; warnings.append("CURVE_UNAVAILABLE")
            else:
                payment_dates = [item["date"] for item in detail_input.get("coupon_schedule") or [] if item.get("date")]
                from bonds.pricing.floater import project_floater_cashflows
                flows = project_floater_cashflows(
                    payment_dates=payment_dates, face=float(row["face_value_per_bond_rub"]),
                    coupon_model=structure.coupon_model, as_of=as_of,
                    maturity_date=str(structure.principal_model.maturity_date), reference_curve=curve,
                )
                context = {"flows": flows, "dirty_price": float(row["dirty_price_per_bond_rub"]),
                           "clean_price": float(row["clean_price_pct"]), "aci": float(row.get("aci_per_bond_rub") or 0),
                           "as_of": as_of, "discount_curve": curve, "reference_curve": curve,
                           "market_value": float(row["dirty_price_per_lot_rub"])}
        if status == AnalysisStatus.FULL:
            analytics = calculate_bond_analytics(structure=structure, context=context)
    if status != AnalysisStatus.FULL:
        warnings.append("STRUCTURE_TERMS_OR_SCHEDULE_INCOMPLETE")
        capabilities = capabilities_for(structure, status)

    cls = structure_class(structure)
    rating_rank = _float(row.get("rating_rank"))
    credit_score = min(100.0, max(0.0, rating_rank / 20 * 100)) if rating_rank is not None else (100.0 if row.get("instrument_type") == "ofz" else None)
    adv = _float(row.get("median_volume_20d_rub"))
    sessions = int(row.get("history_sessions") or 0)
    liquidity_score = min(100.0, (math.log10(max(adv, 1.0)) - 4.0) * 20.0 + min(sessions, 20) * 2.0) if adv is not None else None
    duration = _metric_value(analytics, "effective_duration") or _metric_value(analytics, "modified_duration")
    structure_penalty = float((opportunity_config.get("structure_risk_penalties") or {}).get(cls, 50))
    quality_flags = row.get("data_quality_flags") or []
    data_penalty = min(100.0, 12.5 * len(quality_flags))
    carry = (
        _metric_value(analytics, "ytm_gross") or _metric_value(analytics, "current_coupon_rate")
        or _metric_value(analytics, "current_yield")
    )
    safe_exclusions = [] if row.get("cashflows_deterministic") and cls == "FIXED_BULLET" and not row.get("qualified_only") else [
        code for code, active in {
            "NON_FIXED_BULLET": cls != "FIXED_BULLET", "QUALIFIED_ONLY": row.get("qualified_only"),
            "ANALYTICS_NOT_FULL": status != AnalysisStatus.FULL,
        }.items() if active
    ]
    opportunity_exclusions = [code for code, active in {
        "ANALYTICS_NOT_FULL": status != AnalysisStatus.FULL,
        "RATING_UNAVAILABLE": row.get("instrument_type") != "ofz" and not row.get("rating"),
        "LIQUIDITY_UNAVAILABLE": liquidity_score is None,
    }.items() if active]
    compact = {
        "secid": row["secid"], "isin": row.get("isin"), "name": row.get("name"),
        "issuer_id": row.get("issuer_id"), "issuer_name": row.get("issuer_name"),
        "instrument_type": row.get("instrument_type"), "sector": row.get("sector"),
        "rating": row.get("rating"), "rating_group": row.get("rating_group"),
        "structure_class": cls, "bond_structure_type": legacy_structure_type(structure),
        "analysis_status": status.value, "capabilities": capabilities,
        "clean_price_pct": row.get("clean_price_pct"), "dirty_price_per_lot_rub": row.get("dirty_price_per_lot_rub"),
        "maturity_date": structure.principal_model.maturity_date,
        "next_event_date": (structure.optionality.call_schedule or structure.optionality.put_schedule or ({"date": structure.coupon_model.next_reset_date},))[0].get("date"),
        "primary_metric": None, "primary_metric_label": None,
        "rating_rank": row.get("rating_rank"), "credit_quality_score": credit_score,
        "liquidity_score": round(liquidity_score, 4) if liquidity_score is not None else None,
        "carry_pct": carry, "rate_risk_penalty": min(100.0, float(duration or 0) * 12.5),
        "duration_years": duration,
        "structure_risk_penalty": structure_penalty, "data_quality_penalty": data_penalty,
        "qualified_only": structure.qualified_only, "seniority": structure.seniority.value,
        "safe_portfolio_eligible": not safe_exclusions, "safe_exclusion_codes": safe_exclusions,
        "opportunity_portfolio_eligible": not opportunity_exclusions,
        "opportunity_exclusion_codes": opportunity_exclusions,
        "data_quality_flags": quality_flags, "source_dates": row.get("source_dates") or {},
    }
    primary_map = {
        "FIXED_BULLET": ("ytm_gross", "YTM"), "AMORTIZING_FIXED": ("ytm_gross", "YTM"),
        "PUTTABLE_FIXED": ("yield_to_worst", "YTW"), "CALLABLE_FIXED": ("ytc_if_call", "YTC if call"),
        "FLOATER": ("discount_margin", "Discount Margin"), "PERPETUAL_RESET": ("current_yield", "Current Yield"),
    }
    metric_key, label = primary_map.get(cls, (None, None))
    compact["primary_metric"] = _metric_value(analytics, metric_key) if metric_key else None
    compact["primary_metric_label"] = label
    compact["z_spread_bp"] = _metric_value(analytics, "z_spread")
    compact["discount_margin_bp"] = _metric_value(analytics, "discount_margin")
    compact["yield_to_worst_pct"] = _metric_value(analytics, "yield_to_worst")
    compact["structural_premium_bp"] = _metric_value(analytics, "structural_premium")
    detail = {
        "schema_version": "4.0", "secid": row["secid"], "analysis_status": status.value,
        "identity": {key: row.get(key) for key in ("secid", "isin", "name", "issuer_id", "issuer_name", "board")},
        "structure": structure.to_dict(), "capabilities": capabilities,
        "market": {"clean_price_pct": row.get("clean_price_pct"), "dirty_price_per_bond_rub": row.get("dirty_price_per_bond_rub"),
                   "aci_per_bond_rub": row.get("aci_per_bond_rub"), "as_of": (row.get("source_dates") or {}).get("price")},
        "analytics": analytics, "cashflows": [flow.to_dict() for flow in flows],
        "scenario_lab": _scenario_lab(
            structure_class_name=cls, analytics=analytics, flows=flows,
            dirty_price=_float(row.get("dirty_price_per_bond_rub")), as_of=as_of,
        ),
        "relative_value": None,
        "eligibility": {"safe_portfolio_eligible": compact["safe_portfolio_eligible"],
                        "safe_exclusion_codes": safe_exclusions,
                        "opportunity_portfolio_eligible": compact["opportunity_portfolio_eligible"],
                        "opportunity_exclusion_codes": opportunity_exclusions},
        "liquidity": {"adv_20_rub": row.get("median_volume_20d_rub"), "sessions_traded": row.get("history_sessions"),
                      "bid": None, "ask": None, "bid_ask_spread_bp": None, "depth_status": "UNAVAILABLE"},
        "provenance": {"market": "MOEX ISS", "terms": (terms or {}).get("source"),
                       "curve": curve.metadata() if curve else None, "generated_at": datetime.now().astimezone().isoformat()},
        "warnings": sorted(set(warnings)),
    }
    return compact, detail
