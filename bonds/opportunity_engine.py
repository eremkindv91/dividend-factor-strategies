"""Integer-lot allocator for the separate Opportunities mode."""
from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path

from bonds.official_ratings import RATING_RANK


DEFAULT_CONFIG = Path(__file__).with_name("opportunity_config.json")
COMPLEX_CLASSES = {"FLOATER", "CALLABLE_FIXED", "PUTTABLE_FIXED", "PERPETUAL_RESET", "SUBORDINATED"}


def _eligible(row: dict, profile: dict, budget: float, qualified: bool,
              allow_complex: bool) -> tuple[bool, list[str]]:
    reasons = []
    if row.get("opportunity_portfolio_eligible") is False:
        reasons.extend(row.get("opportunity_exclusion_codes") or ["OPPORTUNITY_INELIGIBLE"])
    if row.get("analysis_status") != "FULL": reasons.append("ANALYTICS_NOT_FULL")
    if row.get("critical_data_conflict"): reasons.append("CRITICAL_DATA_CONFLICT")
    lot = float(row.get("dirty_price_per_lot_rub") or 0)
    if lot <= 0: reasons.append("INVALID_LOT_PRICE")
    if lot > budget * float(profile["max_issue"]): reasons.append("LOT_SIZE_CONCENTRATION")
    if row.get("qualified_only") and not qualified: reasons.append("QUALIFIED_ONLY_DISABLED")
    if not allow_complex and row.get("structure_class") in COMPLEX_CLASSES: reasons.append("COMPLEX_DISABLED")
    if float(row.get("liquidity_score") or 0) < float(profile["minimum_liquidity_score"]): reasons.append("LIQUIDITY_FLOOR")
    rating = row.get("rating")
    floor = profile["minimum_corporate_rating"]
    if row.get("instrument_type") != "ofz" and (
        not rating or RATING_RANK.get(str(rating), -1) < RATING_RANK.get(str(floor), 10**6)
    ):
        reasons.append("RATING_FLOOR")
    if row.get("opportunity_score") is None: reasons.append("SCORE_UNAVAILABLE")
    duration = row.get("duration_years")
    if duration is not None and float(duration) > float(profile.get("max_duration_years", 1e9)):
        reasons.append("DURATION_LIMIT")
    return not reasons, reasons


def allocate_opportunities(rows: list[dict], budget_rub: float, *, qualified: bool = False,
                           allow_complex: bool = True,
                           config_path: str | Path = DEFAULT_CONFIG,
                           profile_key: str = "balanced") -> dict:
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    profile = config["profiles"][profile_key]
    candidates, exclusions = [], defaultdict(int)
    for row in rows:
        ok, reasons = _eligible(row, profile, budget_rub, qualified, allow_complex)
        if ok: candidates.append(row)
        else:
            for reason in reasons: exclusions[reason] += 1
    # A hard minimum liquid core is a feasibility constraint, not a post-hoc
    # diagnostic.  Reserve it first; otherwise a greedy high-score pass can fill
    # issuer/sector/structure caps with illiquid issues and reject an otherwise
    # feasible portfolio.
    candidates.sort(key=lambda row: (
        0 if float(row.get("liquidity_score") or 0) >= 50 else 1,
        -float(row["opportunity_score"]), str(row["secid"]),
    ))
    positions, issuer_used, sector_used, class_used = [], defaultdict(float), defaultdict(float), defaultdict(float)
    invested = complex_used = perp_used = sub_used = qualified_used = low_liq_used = liquid_core = 0.0
    for row in candidates:
        lot_cost = float(row["dirty_price_per_lot_rub"])
        if invested + lot_cost > budget_rub: continue
        issuer, sector, cls = str(row.get("issuer_id")), str(row.get("sector")), str(row.get("structure_class"))
        remaining_caps = [
            budget_rub - invested,
            budget_rub * profile["max_issue"],
            budget_rub * max(0.0, profile["max_issuer"] - issuer_used[issuer]),
            budget_rub * max(0.0, profile["max_sector"] - sector_used[sector]),
            budget_rub * max(0.0, profile["max_single_structure"] - class_used[cls]),
        ]
        if cls in COMPLEX_CLASSES:
            remaining_caps.append(budget_rub * max(0.0, profile["max_complex_total"] - complex_used))
        if cls == "PERPETUAL_RESET":
            remaining_caps.append(budget_rub * max(0.0, profile["max_perpetual"] - perp_used))
        if cls in {"SUBORDINATED", "PERPETUAL_RESET"}:
            remaining_caps.append(budget_rub * max(0.0, profile["max_subordinated"] - sub_used))
        if row.get("qualified_only"):
            remaining_caps.append(budget_rub * max(0.0, profile["max_qualified_only"] - qualified_used))
        if float(row.get("liquidity_score") or 0) < 50:
            remaining_caps.append(budget_rub * max(0.0, profile["max_low_liquidity"] - low_liq_used))
        lots = int((min(remaining_caps) + 1e-9) // lot_cost)
        if lots < 1: continue
        amount, weight = lots * lot_cost, lots * lot_cost / budget_rub
        positions.append({
            "secid": row["secid"], "lots": lots, "amount_rub": round(amount, 2),
            "weight": weight, "opportunity_score": row["opportunity_score"],
            "structure_class": cls,
            "reason_included": row.get("opportunity_reason") or "Высокий structure-aware score внутри своего класса.",
        })
        invested += amount; issuer_used[issuer] += weight; sector_used[sector] += weight; class_used[cls] += weight
        if cls in COMPLEX_CLASSES: complex_used += weight
        if cls == "PERPETUAL_RESET": perp_used += weight
        if cls in {"SUBORDINATED", "PERPETUAL_RESET"}: sub_used += weight
        if row.get("qualified_only"): qualified_used += weight
        if float(row.get("liquidity_score") or 0) < 50: low_liq_used += weight
        else: liquid_core += weight
    status = "OK" if positions and liquid_core >= profile["min_liquid_core"] - 1e-12 else "INFEASIBLE"
    reasons = [] if status == "OK" else ["MIN_LIQUID_CORE_NOT_MET" if positions else "NO_ELIGIBLE_LOTS"]
    if status != "OK":
        # Never publish portfolio-level totals for a candidate allocation that failed
        # a hard constraint.  Diagnostics remain in exclusions/reason_codes.
        positions = []
        invested = complex_used = perp_used = sub_used = qualified_used = low_liq_used = liquid_core = 0.0
        class_used.clear()
    return {
        "schema_version": "4.0", "mode": "opportunities", "status": status,
        "profile": profile_key, "qualified_enabled": qualified, "complex_enabled": allow_complex,
        "budget_rub": budget_rub, "invested_rub": round(invested, 2), "cash_rub": round(budget_rub - invested, 2),
        "positions": positions, "reason_codes": reasons,
        "exclusions": dict(sorted(exclusions.items())),
        "structure_mix": {key: round(value, 8) for key, value in sorted(class_used.items())},
        "risk": {"complex_share": complex_used, "perpetual_share": perp_used,
                 "subordinated_share": sub_used, "qualified_share": qualified_used,
                 "low_liquidity_share": low_liq_used, "liquid_core_share": liquid_core},
    }
