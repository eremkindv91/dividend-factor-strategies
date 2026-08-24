#!/usr/bin/env python3
"""Build and atomically publish Bond Portfolio Lab v3 artifacts."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .integer_allocator import allocate_integer_lots
from .portfolio_engine import build_preset_matrix
from .universe_builder import DEFAULT_CONFIG, DEFAULT_ISSUER_MASTER, build_live_universe, load_json
from .validation import quality_gate, validate_integer_allocation, validate_target_portfolio

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "site" / "bonds"


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def _persist_verified_issuer_records(universe: dict, path: Path = DEFAULT_ISSUER_MASTER) -> int:
    resolved = (((universe.get("source_status") or {}).get("sector_mapping") or {})
                .get("fns_enrichment") or {}).get("resolved") or []
    if not resolved:
        return 0
    master = load_json(path)
    issuers = dict(master.get("issuers") or {})
    changed = 0
    for record in resolved:
        inn = str(record.get("issuer_inn") or "").strip()
        if not (inn.isdigit() and len(inn) == 10):
            continue
        item = {key: value for key, value in record.items() if key != "issuer_inn"}
        if item.get("sector_source") != "fns_main_okved" or not item.get("okved_main"):
            continue
        if issuers.get(inn) != item:
            issuers[inn] = item
            changed += 1
    if changed:
        master["issuers"] = issuers
        _atomic_json(path, master)
    return changed


def build_and_publish(
    *, load_board, http_json, iss: str, ratings: dict, ratings_meta: dict, gcurve_rate,
    output_dir: Path = OUT, config_path: Path = DEFAULT_CONFIG,
    curve_points: list[tuple[float, float]] | None = None,
) -> dict:
    universe = build_live_universe(
        load_board=load_board,
        http_json=http_json,
        iss=iss,
        ratings=ratings,
        ratings_meta=ratings_meta,
        gcurve_rate=gcurve_rate,
        config_path=config_path,
        include_v4_inputs=True,
    )
    v4_inputs = universe.pop("_v4_inputs", {})
    persisted_issuer_records = _persist_verified_issuer_records(universe)
    gate = quality_gate(universe, config_path)
    validation = {
        "schema_version": "3.0",
        "quality_gate": gate,
        "universe_summary": {
            "bonds": len(universe.get("bonds") or []),
            "corporate": sum(row.get("instrument_type") == "corp" for row in universe.get("bonds") or []),
            "ofz": sum(row.get("instrument_type") == "ofz" for row in universe.get("bonds") or []),
            "sector_mapping": ((universe.get("source_status") or {}).get("sector_mapping") or {}),
            "persisted_issuer_records": persisted_issuer_records,
        },
        "presets": {},
        "status": gate["status"],
    }
    if gate["status"] != "PASS":
        _atomic_json(output_dir / "portfolio_validation.json", validation)
        return validation

    presets = build_preset_matrix(universe, config_path)
    config = load_json(config_path)
    allocations: dict[str, dict] = {}
    critical_errors: list[str] = []
    unavailable_presets: list[str] = []
    for key, target in presets["presets"].items():
        if target.get("status") == "INFEASIBLE":
            validation["presets"][key] = {
                "status": "UNAVAILABLE",
                "target_errors": [],
                "target_status": "INFEASIBLE",
                "reason_codes": target.get("reason_codes") or [],
                "solver_message": target.get("solver_message"),
                "eligible_issues": target.get("eligible_issues"),
                "eligible_issuers": target.get("eligible_issuers"),
                "exclusions": target.get("exclusions") or {},
                "candidate_diagnostics": target.get("candidate_diagnostics") or {},
            }
            unavailable_presets.append(key)
            continue
        target_errors = validate_target_portfolio(target, universe, config_path)
        if target_errors:
            validation["presets"][key] = {
                "status": "FAIL",
                "target_errors": target_errors,
                "target_status": target.get("status"),
                "reason_codes": target.get("reason_codes") or [],
                "solver_message": target.get("solver_message"),
                "eligible_issues": target.get("eligible_issues"),
                "eligible_issuers": target.get("eligible_issuers"),
                "exclusions": target.get("exclusions") or {},
                "candidate_diagnostics": target.get("candidate_diagnostics") or {},
            }
            critical_errors.extend(f"{key}:{error}" for error in target_errors)
            continue
        allocation = allocate_integer_lots(target, universe, config["default_budget_rub"], config_path)
        allocation_errors = validate_integer_allocation(allocation, target, universe, config_path)
        if allocation_errors:
            # A valid continuous target can still be impossible to express in
            # integer lots for today's prices and configured budget. Keep the
            # preset explicitly unavailable without discarding every other
            # independently validated allocation from the same fresh run.
            validation["presets"][key] = {
                "status": "UNAVAILABLE",
                "target_errors": [],
                "target_status": target.get("status"),
                "allocation_errors": allocation_errors,
                "reason_codes": allocation.get("reason_codes") or allocation_errors,
                "solver_message": allocation.get("solver_message"),
            }
            unavailable_presets.append(key)
            continue
        validation["presets"][key] = {
            "status": "PASS",
            "target_errors": [],
            "allocation_errors": [],
        }
        allocations[key] = allocation

    if critical_errors:
        validation.update({"status": "FAIL", "critical_errors": critical_errors})
        _atomic_json(output_dir / "portfolio_validation.json", validation)
        return validation

    presets["allocations"] = allocations
    validation["status"] = "PASS"
    validation["available_presets"] = len(allocations)
    validation["unavailable_presets"] = unavailable_presets
    _atomic_json(output_dir / "universe.json", universe)
    _atomic_json(output_dir / "portfolio_presets.json", presets)
    _atomic_json(output_dir / "portfolio_validation.json", validation)
    _atomic_json(output_dir / "portfolio_last_valid.json", {
        "schema_version": "3.0",
        "generated_at": universe["generated_at"],
        "universe_hash": presets["universe_hash"],
        "profiles": presets["profiles"],
        "horizons": presets["horizons"],
        "costs": presets["costs"],
        "budget_limits": presets["budget_limits"],
        "presets": presets["presets"],
        "allocations": allocations,
    })
    from .pipeline_v4 import build_v4_artifacts
    validation["bond_analytics_v4"] = build_v4_artifacts(
        universe, detail_inputs=v4_inputs, curve_points=curve_points, output_dir=output_dir,
    )
    _atomic_json(output_dir / "portfolio_validation.json", validation)
    return validation
