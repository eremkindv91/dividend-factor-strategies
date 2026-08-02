#!/usr/bin/env python3
"""Deterministic MILP target-weight engine for Bond Portfolio Lab 3.0."""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from .universe_builder import RATING_RANK, load_json

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "portfolio_config.json"


def canonical_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _eligible(row: dict, profile: dict, config: dict, budget_rub: float) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    allowed = config["allowed_instruments"]
    if row.get("duration_type") != "modified_duration_effective_annual" or not _is_number(row.get("duration_value")):
        reasons.append("modified_duration_required")
    if not _is_number(row.get("dirty_price_per_lot_rub")) or float(row["dirty_price_per_lot_rub"]) <= 0:
        reasons.append("dirty_lot_price_required")
    if not _is_number(row.get("g_spread_pp")):
        reasons.append("g_spread_required")
    if row.get("coupon_type") != "fixed" and not allowed.get("floaters", False):
        reasons.append("complex_coupon_excluded")
    if row.get("amortizing") and not allowed.get("amortizing", False):
        reasons.append("amortizing_excluded")
    if row.get("has_put_offer") and not allowed.get("put_offer", False):
        reasons.append("put_offer_excluded")
    if row.get("has_call") and not allowed.get("callable", False):
        reasons.append("callable_excluded")
    if row.get("qualified_only") and not allowed.get("qualified_only", False):
        reasons.append("qualified_only_excluded")
    if int(row.get("history_sessions") or 0) < int(profile["minimum_trading_sessions"]):
        reasons.append("insufficient_liquidity_history")
    median_volume = float(row.get("median_volume_20d_rub") or 0.0)
    if median_volume < float(profile["minimum_median_volume_20d_rub"]):
        reasons.append("median_volume_below_floor")
    liquidity_weight = median_volume * float(profile["maximum_participation_rate"]) / budget_rub
    if liquidity_weight + 1e-12 < float(profile["minimum_position"]):
        reasons.append("liquidity_cannot_support_minimum_position")
    if row.get("instrument_type") == "corp":
        floor = RATING_RANK[profile["minimum_corporate_rating"]]
        if row.get("rating_rank") is None or int(row["rating_rank"]) < floor:
            reasons.append("rating_below_floor_or_missing")
    return not reasons, reasons


def adjusted_carry(row: dict, profile: dict, config: dict) -> tuple[float, dict]:
    penalties = config["penalties"]
    spread = float(row.get("g_spread_pp") or 0.0)
    rank = row.get("rating_rank")
    credit = 0.0 if row.get("instrument_type") == "ofz" or rank is None else max(0, 20 - int(rank)) * float(penalties["credit_per_notch_pp"])
    volume_floor = max(float(profile["minimum_median_volume_20d_rub"]), 1.0)
    volume = max(float(row.get("median_volume_20d_rub") or 0.0), 1.0)
    liquidity = max(0.0, 1.0 - min(volume / (5.0 * volume_floor), 1.0)) * float(penalties["liquidity_pp"])
    new_issue = float(penalties["new_issue_pp"]) if row.get("new_placement") else 0.0
    quality = min(len(row.get("data_quality_flags") or []), 3) * float(penalties["data_quality_pp"])
    complexity = float(penalties["complexity_pp"]) if any((row.get("amortizing"), row.get("has_put_offer"), row.get("has_call"), row.get("coupon_type") != "fixed")) else 0.0
    breakdown = {
        "net_g_spread_pp": spread,
        "credit_penalty_pp": credit,
        "liquidity_penalty_pp": liquidity,
        "new_issue_penalty_pp": new_issue,
        "data_quality_penalty_pp": quality,
        "complexity_penalty_pp": complexity,
    }
    return spread - credit - liquidity - new_issue - quality - complexity, breakdown


def _reason_included(row: dict, profile: dict, target: float) -> str:
    if row.get("instrument_type") == "ofz":
        return "Добавлена для доли ОФЗ, контроля процентного риска и лестницы погашений."
    parts = [f"ликвидный выпуск {row.get('rating') or 'без рейтинга'}"]
    if _is_number(row.get("excess_spread_pp")) and float(row["excess_spread_pp"]) > 0:
        parts.append(f"G-spread на {float(row['excess_spread_pp']):.1f} п.п. выше peer benchmark")
    duration = float(row.get("duration_value") or 0.0)
    if abs(duration - target) <= 0.75:
        parts.append(f"дюрация близка к цели {target:.1f} года")
    return "Добавлена как " + ", ".join(parts) + "."


def _candidate_diagnostics(candidates: list[dict], horizon: dict) -> dict:
    durations = sorted(float(row["duration_value"]) for row in candidates)
    within = [
        row for row in candidates
        if float(horizon["min"]) <= float(row["duration_value"]) <= float(horizon["max"])
    ]
    by_instrument = defaultdict(int)
    by_maturity = defaultdict(int)
    issuer_min_duration: dict[str, float] = {}
    for row in candidates:
        by_instrument[str(row.get("instrument_type") or "unknown")] += 1
        by_maturity[str(row.get("maturity_date") or "")[:4]] += 1
        issuer_id = str(row["issuer_id"])
        duration = float(row["duration_value"])
        issuer_min_duration[issuer_id] = min(issuer_min_duration.get(issuer_id, duration), duration)
    return {
        "duration_min": min(durations) if durations else None,
        "duration_median": float(np.median(durations)) if durations else None,
        "duration_max": max(durations) if durations else None,
        "issues_inside_duration_corridor": len(within),
        "issuers_inside_duration_corridor": len({row["issuer_id"] for row in within}),
        "instrument_counts": dict(sorted(by_instrument.items())),
        "maturity_year_counts": dict(sorted(by_maturity.items())),
        "issuer_min_duration": dict(sorted(issuer_min_duration.items(), key=lambda item: item[1])),
        "candidates": [
            {
                "secid": row["secid"],
                "issuer_id": row["issuer_id"],
                "instrument_type": row["instrument_type"],
                "sector": row.get("sector"),
                "duration": row["duration_value"],
                "maturity_year": str(row.get("maturity_date") or "")[:4],
                "new_placement": bool(row.get("new_placement")),
            }
            for row in sorted(candidates, key=lambda item: (float(item["duration_value"]), item["secid"]))
        ],
    }


class _ConstraintBuilder:
    def __init__(self, size: int):
        self.size = size
        self.rows: list[np.ndarray] = []
        self.lower: list[float] = []
        self.upper: list[float] = []

    def add(self, entries: dict[int, float], lower: float = -np.inf, upper: float = np.inf) -> None:
        row = np.zeros(self.size, dtype=float)
        for index, value in entries.items():
            row[index] = value
        self.rows.append(row)
        self.lower.append(lower)
        self.upper.append(upper)

    def build(self) -> LinearConstraint:
        return LinearConstraint(np.vstack(self.rows), np.array(self.lower), np.array(self.upper))


def solve_target_portfolio(
    universe: dict,
    profile_key: str,
    horizon_key: str,
    budget_rub: float | None = None,
    config_path: str | Path = DEFAULT_CONFIG,
) -> dict:
    config = load_json(config_path)
    profile = config["profiles"][profile_key]
    horizon = config["horizons"][horizon_key]
    budget_rub = float(budget_rub or config["default_budget_rub"])
    exclusions = defaultdict(int)
    candidates: list[dict] = []
    for row in sorted(universe.get("bonds") or [], key=lambda item: str(item.get("secid"))):
        ok, reasons = _eligible(row, profile, config, budget_rub)
        if ok:
            candidates.append(row)
        else:
            for reason in reasons:
                exclusions[reason] += 1

    issuer_ids = sorted({str(row["issuer_id"]) for row in candidates})
    if len(candidates) < int(profile["minimum_issues"]) or len(issuer_ids) < int(profile["minimum_issuers"]):
        return {
            "status": "INFEASIBLE",
            "reason_codes": ["insufficient_eligible_issues_or_issuers"],
            "profile": profile_key,
            "horizon": horizon_key,
            "eligible_issues": len(candidates),
            "eligible_issuers": len(issuer_ids),
            "exclusions": dict(sorted(exclusions.items())),
            "candidate_diagnostics": _candidate_diagnostics(candidates, horizon),
            "target_positions": [],
        }

    n = len(candidates)
    issuer_index = {issuer_id: index for index, issuer_id in enumerate(issuer_ids)}
    m = len(issuer_ids)
    w0, y0, z0 = 0, n, 2 * n
    cash_i, dplus_i, dminus_i = 2 * n + m, 2 * n + m + 1, 2 * n + m + 2
    size = 2 * n + m + 3

    objective = np.zeros(size, dtype=float)
    carry_breakdown: list[dict] = []
    for index, row in enumerate(candidates):
        carry, breakdown = adjusted_carry(row, profile, config)
        objective[w0 + index] = -carry
        carry_breakdown.append(breakdown)
    objective[cash_i] = float(config["penalties"]["cash_pp"])
    objective[dplus_i] = objective[dminus_i] = float(config["penalties"]["duration_deviation_pp"])

    lower_bounds = np.zeros(size, dtype=float)
    upper_bounds = np.full(size, np.inf, dtype=float)
    upper_bounds[w0:w0 + n] = float(profile["max_issue"])
    upper_bounds[y0:y0 + n] = 1.0
    upper_bounds[z0:z0 + m] = 1.0
    lower_bounds[cash_i] = float(profile["cash_min"])
    upper_bounds[cash_i] = float(profile["cash_max"])
    integrality = np.zeros(size, dtype=int)
    integrality[y0:y0 + n] = 1
    integrality[z0:z0 + m] = 1

    cb = _ConstraintBuilder(size)
    cb.add({**{w0 + i: 1.0 for i in range(n)}, cash_i: 1.0}, 1.0, 1.0)
    min_position = float(profile["minimum_position"])
    for index, row in enumerate(candidates):
        liquidity_cap = min(
            float(profile["max_issue"]),
            float(row["median_volume_20d_rub"]) * float(profile["maximum_participation_rate"]) / budget_rub,
        )
        cb.add({w0 + index: 1.0, y0 + index: -liquidity_cap}, upper=0.0)
        cb.add({w0 + index: 1.0, y0 + index: -min_position}, lower=0.0)
        issuer_pos = issuer_index[str(row["issuer_id"])]
        cb.add({y0 + index: 1.0, z0 + issuer_pos: -1.0}, upper=0.0)

    by_issuer: dict[str, list[int]] = defaultdict(list)
    by_sector: dict[str, list[int]] = defaultdict(list)
    by_maturity: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(candidates):
        by_issuer[str(row["issuer_id"])].append(index)
        by_sector[str(row.get("sector") or "unknown")].append(index)
        by_maturity[str(row.get("maturity_date") or "")[:4]].append(index)

    for issuer_id, indexes in by_issuer.items():
        z_index = z0 + issuer_index[issuer_id]
        lower_entry = {w0 + index: 1.0 for index in indexes}
        lower_entry[z_index] = -min_position
        cb.add(lower_entry, lower=0.0)
        if not issuer_id.startswith("sovereign:"):
            upper_entry = {w0 + index: 1.0 for index in indexes}
            upper_entry[z_index] = -float(profile["max_issuer"])
            cb.add(upper_entry, upper=0.0)
    cb.add({z0 + i: 1.0 for i in range(m)}, lower=float(profile["minimum_issuers"]))
    cb.add({y0 + i: 1.0 for i in range(n)}, lower=float(profile["minimum_issues"]))

    target = float(horizon["target"])
    duration_equation = {w0 + i: float(row["duration_value"]) - target for i, row in enumerate(candidates)}
    duration_equation[dplus_i] = -1.0
    duration_equation[dminus_i] = 1.0
    cb.add(duration_equation, 0.0, 0.0)
    cb.add({w0 + i: float(row["duration_value"]) - float(horizon["min"]) for i, row in enumerate(candidates)}, lower=0.0)
    cb.add({w0 + i: float(row["duration_value"]) - float(horizon["max"]) for i, row in enumerate(candidates)}, upper=0.0)

    ofz_indexes = [i for i, row in enumerate(candidates) if row.get("instrument_type") == "ofz"]
    cb.add({w0 + i: 1.0 for i in ofz_indexes}, lower=float(profile["minimum_ofz"]))
    bbb_indexes = [i for i, row in enumerate(candidates) if row.get("rating_group") == "BBB"]
    cb.add({w0 + i: 1.0 for i in bbb_indexes}, upper=float(profile["max_bbb"]))
    new_indexes = [i for i, row in enumerate(candidates) if row.get("new_placement")]
    cb.add({w0 + i: 1.0 for i in new_indexes}, upper=float(profile["maximum_new_issues"]))

    for sector, indexes in by_sector.items():
        if sector == "Государственные облигации":
            continue
        cap = float(profile["max_unknown_sector"] if sector == "unknown" else profile["max_sector"])
        cb.add({w0 + i: 1.0 for i in indexes}, upper=cap)
    for indexes in by_maturity.values():
        cb.add({w0 + i: 1.0 for i in indexes}, upper=float(profile["maximum_maturity_year_bucket"]))

    options = {
        "time_limit": float(config["solver"]["time_limit_seconds"]),
        "mip_rel_gap": float(config["solver"]["mip_relative_gap"]),
        "presolve": True,
    }
    result = milp(
        c=objective,
        integrality=integrality,
        bounds=Bounds(lower_bounds, upper_bounds),
        constraints=cb.build(),
        options=options,
    )
    if not result.success or result.x is None:
        return {
            "status": "INFEASIBLE" if int(result.status) == 2 else "EXECUTION_FAILED",
            "reason_codes": ["milp_infeasible" if int(result.status) == 2 else "milp_failed"],
            "solver_message": str(result.message),
            "profile": profile_key,
            "horizon": horizon_key,
            "eligible_issues": len(candidates),
            "eligible_issuers": len(issuer_ids),
            "exclusions": dict(sorted(exclusions.items())),
            "candidate_diagnostics": _candidate_diagnostics(candidates, horizon),
            "target_positions": [],
        }

    weights = result.x[w0:w0 + n]
    positions = []
    for index, weight in enumerate(weights):
        if weight <= 1e-8:
            continue
        row = candidates[index]
        positions.append({
            "secid": row["secid"],
            "issuer_id": row["issuer_id"],
            "issuer_name": row["issuer_name"],
            "name": row["name"],
            "instrument_type": row["instrument_type"],
            "sector": row["sector"],
            "rating": row.get("rating"),
            "rating_group": row.get("rating_group"),
            "target_weight": round(float(weight), 10),
            "duration_value": row["duration_value"],
            "dirty_price_per_lot_rub": row["dirty_price_per_lot_rub"],
            "ytm_gross_pct": row.get("ytm_gross_pct"),
            "ytm_net_est_pct": row.get("ytm_net_est_pct"),
            "g_spread_pp": row.get("g_spread_pp"),
            "peer_spread_pp": row.get("peer_spread_pp"),
            "excess_spread_pp": row.get("excess_spread_pp"),
            "median_volume_20d_rub": row.get("median_volume_20d_rub"),
            "maturity_date": row.get("maturity_date"),
            "new_placement": bool(row.get("new_placement")),
            "data_quality_flags": row.get("data_quality_flags") or [],
            "adjusted_carry_pp": round(-float(objective[w0 + index]), 6),
            "penalty_decomposition": carry_breakdown[index],
            "reason_included": _reason_included(row, profile, target),
        })
    positions.sort(key=lambda item: (-item["target_weight"], item["secid"]))
    invested_weight = sum(item["target_weight"] for item in positions)
    weighted_duration = sum(item["target_weight"] * float(item["duration_value"]) for item in positions) / invested_weight
    return {
        "schema_version": "3.0",
        "status": "OPTIMAL" if int(result.status) == 0 else "FEASIBLE",
        "solver": "scipy.optimize.milp-highs",
        "solver_status": int(result.status),
        "solver_message": str(result.message),
        "objective_value": round(float(result.fun), 8),
        "config_hash": canonical_hash(config),
        "universe_hash": canonical_hash(universe.get("bonds") or []),
        "profile": profile_key,
        "profile_label": profile["label"],
        "horizon": horizon_key,
        "horizon_label": horizon["label"],
        "target_duration": target,
        "duration_corridor": [float(horizon["min"]), float(horizon["max"])],
        "cash_target_weight": round(float(result.x[cash_i]), 10),
        "portfolio_duration": round(weighted_duration, 6),
        "eligible_issues": len(candidates),
        "eligible_issuers": len(issuer_ids),
        "exclusions": dict(sorted(exclusions.items())),
        "target_positions": positions,
    }


def build_preset_matrix(universe: dict, config_path: str | Path = DEFAULT_CONFIG) -> dict:
    config = load_json(config_path)
    presets: dict[str, dict] = {}
    for profile_key in config["profiles"]:
        for horizon_key in config["horizons"]:
            key = f"{profile_key}:{horizon_key}"
            presets[key] = solve_target_portfolio(
                universe, profile_key, horizon_key, config["default_budget_rub"], config_path
            )
    return {
        "schema_version": "3.0",
        "generated_at": universe.get("generated_at"),
        "universe_hash": canonical_hash(universe.get("bonds") or []),
        "default_profile": "balanced",
        "default_horizon": "3y",
        "profiles": config["profiles"],
        "horizons": config["horizons"],
        "costs": config["costs"],
        "budget_limits": {"minimum_rub": 250000, "maximum_rub": 100000000, "step_rub": 50000},
        "presets": presets,
    }
