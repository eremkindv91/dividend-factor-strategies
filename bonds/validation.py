#!/usr/bin/env python3
"""Schema, source-quality and portfolio invariant checks for bond artifacts."""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from .universe_builder import RATING_RANK, load_json

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "portfolio_config.json"


def _age_days(value: str | None, today: date) -> int | None:
    if not value:
        return None
    try:
        return (today - date.fromisoformat(str(value)[:10])).days
    except ValueError:
        return None


def validate_universe_schema(universe: dict) -> list[str]:
    errors: list[str] = []
    if universe.get("schema_version") != "3.0":
        errors.append("schema_version_invalid")
    if not isinstance(universe.get("as_of"), dict):
        errors.append("as_of_missing")
    bonds = universe.get("bonds")
    if not isinstance(bonds, list) or not bonds:
        return errors + ["bonds_empty"]
    required = {
        "secid", "instrument_type", "issuer_id", "sector", "face_value_per_bond_rub",
        "lot_size", "clean_price_pct", "aci_per_bond_rub", "dirty_price_per_bond_rub",
        "dirty_price_per_lot_rub", "duration_value", "duration_type", "duration_source",
        "maturity_date", "median_volume_20d_rub", "data_quality_flags",
    }
    seen = set()
    for index, row in enumerate(bonds):
        prefix = str(row.get("secid") or index)
        missing = sorted(required - set(row))
        if missing:
            errors.append(f"{prefix}:missing:{','.join(missing)}")
            continue
        if row["secid"] in seen:
            errors.append(f"{prefix}:duplicate_secid")
        seen.add(row["secid"])
        if row["instrument_type"] not in {"corp", "ofz"}:
            errors.append(f"{prefix}:instrument_type_invalid")
        face = float(row["face_value_per_bond_rub"])
        clean = float(row["clean_price_pct"])
        aci = float(row["aci_per_bond_rub"])
        lot_size = int(row["lot_size"])
        dirty_bond = clean / 100.0 * face + aci
        dirty_lot = dirty_bond * lot_size
        if abs(dirty_bond - float(row["dirty_price_per_bond_rub"])) > 0.011:
            errors.append(f"{prefix}:dirty_price_per_bond_mismatch")
        if abs(dirty_lot - float(row["dirty_price_per_lot_rub"])) > 0.011:
            errors.append(f"{prefix}:dirty_price_per_lot_mismatch")
        if lot_size < 1 or face <= 0 or clean <= 0:
            errors.append(f"{prefix}:price_contract_invalid")
    return errors

def quality_gate(universe: dict, config_path: str | Path = DEFAULT_CONFIG, today: date | None = None) -> dict:
    today = today or date.today()
    config = load_json(config_path)
    thresholds = config["quality_gate"]
    bonds = universe.get("bonds") or []
    corporate = [row for row in bonds if row.get("instrument_type") == "corp"]
    schema_errors = validate_universe_schema(universe)

    def coverage(rows, predicate) -> float:
        return sum(1 for row in rows if predicate(row)) / len(rows) if rows else 0.0

    metrics = {
        "rating_coverage": coverage(corporate, lambda row: row.get("rating_rank") is not None),
        "sector_coverage": coverage(corporate, lambda row: row.get("sector") not in {None, "", "unknown"}),
        "modified_duration_coverage": coverage(bonds, lambda row: row.get("duration_type") == "modified_duration_effective_annual"),
        "liquidity_history_coverage": coverage(bonds, lambda row: int(row.get("history_sessions") or 0) >= 10),
    }
    source_ratings = ((universe.get("source_status") or {}).get("ratings") or {}).get("sources") or {}
    live_rating_sources = sum(1 for item in source_ratings.values() if item.get("status") == "ok" and item.get("mode") == "live")
    metrics["live_rating_sources"] = live_rating_sources
    as_of = universe.get("as_of") or {}
    ages = {
        "prices": _age_days(as_of.get("prices"), today),
        "curve": _age_days(as_of.get("curve"), today),
        "ratings": _age_days(as_of.get("ratings"), today),
        "history": _age_days(as_of.get("history"), today),
    }
    failures = list(schema_errors)
    for name, maximum in (
        ("prices", thresholds["max_price_age_days"]),
        ("curve", thresholds["max_curve_age_days"]),
        ("ratings", thresholds["max_rating_check_age_days"]),
    ):
        if ages[name] is None or ages[name] > int(maximum):
            failures.append(f"{name}_stale_or_missing")
    for metric, threshold in (
        ("rating_coverage", thresholds["minimum_rating_coverage"]),
        ("sector_coverage", thresholds["minimum_sector_coverage"]),
        ("modified_duration_coverage", thresholds["minimum_modified_duration_coverage"]),
        ("liquidity_history_coverage", thresholds["minimum_liquidity_history_coverage"]),
    ):
        if metrics[metric] + 1e-12 < float(threshold):
            failures.append(f"{metric}_below_gate")
    if live_rating_sources < int(thresholds["minimum_live_rating_sources"]):
        failures.append("live_rating_sources_below_gate")
    return {
        "status": "PASS" if not failures else "FAIL",
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "failures": failures,
        "metrics": metrics,
        "ages_days": ages,
        "thresholds": thresholds,
    }


def validate_target_portfolio(target: dict, universe: dict, config_path: str | Path = DEFAULT_CONFIG) -> list[str]:
    if target.get("status") not in {"OPTIMAL", "FEASIBLE"}:
        return ["target_not_feasible"]
    config = load_json(config_path)
    profile = config["profiles"][target["profile"]]
    horizon = config["horizons"][target["horizon"]]
    by_secid = {row["secid"]: row for row in universe.get("bonds") or []}
    positions = target.get("target_positions") or []
    tolerance = float(config["solver"]["numerical_tolerance"])
    errors: list[str] = []
    cash = float(target.get("cash_target_weight") or 0.0)
    total = sum(float(item["target_weight"]) for item in positions) + cash
    if abs(total - 1.0) > tolerance:
        errors.append("weights_plus_cash_not_one")
    by_issuer = defaultdict(float)
    by_sector = defaultdict(float)
    by_year = defaultdict(float)
    ofz = bbb = new = duration_sum = invested = 0.0
    for item in positions:
        row = by_secid.get(item["secid"])
        if not row:
            errors.append(f"{item['secid']}:missing_from_universe")
            continue
        weight = float(item["target_weight"])
        if weight > float(profile["max_issue"]) + tolerance or weight < float(profile["minimum_position"]) - tolerance:
            errors.append(f"{item['secid']}:position_weight_violation")
        by_issuer[row["issuer_id"]] += weight
        by_sector[row.get("sector") or "unknown"] += weight
        by_year[str(row.get("maturity_date") or "")[:4]] += weight
        if row["instrument_type"] == "ofz":
            ofz += weight
        if row.get("rating_group") == "BBB":
            bbb += weight
        if row.get("new_placement"):
            new += weight
        invested += weight
        duration_sum += weight * float(row["duration_value"])
    for issuer_id, weight in by_issuer.items():
        if not issuer_id.startswith("sovereign:") and weight > float(profile["max_issuer"]) + tolerance:
            errors.append(f"issuer_cap:{issuer_id}")
    for sector, weight in by_sector.items():
        if sector == "Государственные облигации":
            continue
        cap = float(profile["max_unknown_sector"] if sector == "unknown" else profile["max_sector"])
        if weight > cap + tolerance:
            errors.append(f"sector_cap:{sector}")
    if ofz < float(profile["minimum_ofz"]) - tolerance:
        errors.append("minimum_ofz_violation")
    if bbb > float(profile["max_bbb"]) + tolerance:
        errors.append("bbb_cap_violation")
    if new > float(profile["maximum_new_issues"]) + tolerance:
        errors.append("new_issue_cap_violation")
    if any(weight > float(profile["maximum_maturity_year_bucket"]) + tolerance for weight in by_year.values()):
        errors.append("maturity_year_cap_violation")
    if len(by_issuer) < int(profile["minimum_issuers"]):
        errors.append("minimum_issuers_violation")
    if len(positions) < int(profile["minimum_issues"]):
        errors.append("minimum_issues_violation")
    duration = duration_sum / invested if invested else 0.0
    if duration < float(horizon["min"]) - tolerance or duration > float(horizon["max"]) + tolerance:
        errors.append("duration_corridor_violation")
    return errors


def validate_integer_allocation(allocation: dict, target: dict, universe: dict, config_path: str | Path = DEFAULT_CONFIG) -> list[str]:
    if allocation.get("status") != "VALIDATED":
        return ["allocation_not_validated"]
    errors: list[str] = []
    budget = float(allocation["budget_rub"])
    spent = float(allocation["invested_with_costs_rub"])
    cash = float(allocation["cash_rub"])
    if spent + cash > budget + 0.01 or abs(spent + cash - budget) > 0.011:
        errors.append("budget_reconciliation_failed")
    if any(int(item.get("lots") or 0) < 1 for item in allocation.get("positions") or []):
        errors.append("nonpositive_lot_count")
    # Reuse target validation for the target weights; allocation-specific caps are
    # enforced in the integer MILP and checked below against actual weights.
    by_secid = {row["secid"]: row for row in universe.get("bonds") or []}
    config = load_json(config_path)
    profile = config["profiles"][target["profile"]]
    by_issuer = defaultdict(float)
    by_sector = defaultdict(float)
    for item in allocation.get("positions") or []:
        row = by_secid[item["secid"]]
        weight = float(item["actual_weight"])
        if weight > float(profile["max_issue"]) + 0.01:
            errors.append(f"actual_issue_cap:{item['secid']}")
        by_issuer[row["issuer_id"]] += weight
        by_sector[row.get("sector") or "unknown"] += weight
    for issuer_id, weight in by_issuer.items():
        if not issuer_id.startswith("sovereign:") and weight > float(profile["max_issuer"]) + 0.01:
            errors.append(f"actual_issuer_cap:{issuer_id}")
    for sector, weight in by_sector.items():
        if sector == "Государственные облигации":
            continue
        cap = float(profile["max_unknown_sector"] if sector == "unknown" else profile["max_sector"])
        if weight > cap + 0.01:
            errors.append(f"actual_sector_cap:{sector}")
    return errors
