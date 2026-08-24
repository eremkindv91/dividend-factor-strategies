#!/usr/bin/env python3
"""Build additive Bond Analytics Engine v4 artifacts from the validated v3 universe."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from bonds.analytics.opportunity_score import score_opportunities
from bonds.analytics.relative_value import attach_relative_value
from bonds.curves import CurveProvider
from bonds.detail_builder import build_detail
from bonds.opportunity_engine import allocate_opportunities
from bonds.terms_registry import load_verified_terms


OPPORTUNITY_METRIC_LABELS = {
    "yield_to_worst_pct": "Доходность к худшему сценарию",
    "z_spread_bp": "Z-spread",
    "discount_margin_bp": "Discount Margin",
    "current_yield_pct": "Текущая доходность",
}
REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "site" / "bonds"
CONFIG = Path(__file__).with_name("opportunity_config.json")


def _stable_hash(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            handle.write("\n")
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def build_opportunity_variants(rows: list[dict], config: dict) -> dict:
    variants = {}
    for profile_key in config["profiles"]:
        for budget in (250_000.0, 1_000_000.0, 3_000_000.0):
            for qualified in (False, True):
                for allow_complex in (False, True):
                    key = f"{profile_key}:{int(budget)}:{int(qualified)}:{int(allow_complex)}"
                    variants[key] = allocate_opportunities(
                        rows, budget, qualified=qualified, allow_complex=allow_complex,
                        profile_key=profile_key,
                    )
    return {
        "schema_version": "4.0", "mode": "opportunities",
        "default_key": "balanced:1000000:0:1",
        "allocations": variants,
        "available_profiles": list(config["profiles"]),
        "available_budgets_rub": [250000, 1000000, 3000000],
    }


def refresh_opportunities_from_compact(output_dir: Path = OUT) -> dict:
    compact = json.loads((output_dir / "universe_v4.json").read_text(encoding="utf-8"))
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    payload = build_opportunity_variants(compact.get("bonds") or [], config)
    _atomic_json(output_dir / "portfolio_opportunities.json", payload)
    return payload


def build_v4_artifacts(universe: dict, *, detail_inputs: dict | None = None,
                       curve_points: list[tuple[float, float]] | None = None,
                       output_dir: Path = OUT) -> dict:
    if universe.get("schema_version") != "3.0":
        raise ValueError("v4 migration requires a validated v3 universe")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    curve_as_of = date.fromisoformat((universe.get("as_of") or {}).get("curve") or date.today().isoformat())
    curve = CurveProvider(curve_points, as_of=curve_as_of, source="MOEX KBD", curve_id="OFZ_KBD") if curve_points else None
    as_of = date.fromisoformat((universe.get("as_of") or {}).get("prices") or curve_as_of.isoformat())
    rows, details = [], {}
    for row in universe.get("bonds") or []:
        secid = str(row.get("secid") or "")
        terms = load_verified_terms(secid)
        compact, detail = build_detail(
            row, detail_input=(detail_inputs or {}).get(secid), terms=terms,
            curve=curve, as_of=as_of, opportunity_config=config,
        )
        rows.append(compact)
        details[secid] = detail
    attach_relative_value(rows)
    for row in rows:
        rv = row.get("relative_value") or {}
        if rv.get("status") != "CALCULATED":
            row["opportunity_portfolio_eligible"] = False
            if "RELATIVE_VALUE_UNAVAILABLE" not in row["opportunity_exclusion_codes"]:
                row["opportunity_exclusion_codes"].append("RELATIVE_VALUE_UNAVAILABLE")
        details[row["secid"]]["relative_value"] = rv
    score_opportunities(rows, config["score_weights"])
    for row in rows:
        rv = row.get("relative_value") or {}
        metric = rv.get("metric") or "structure metric"
        if rv.get("status") == "CALCULATED":
            metric_label = OPPORTUNITY_METRIC_LABELS.get(metric, metric)
            row["opportunity_reason"] = (
                f"{metric_label}: {rv['percentile']:.0f}-й процентиль среди сопоставимых "
                f"выпусков; итоговая оценка {row.get('opportunity_score'):.1f}/100."
            )
        details[row["secid"]]["opportunity_score"] = row.get("opportunity_score_decomposition")
        details[row["secid"]]["eligibility"]["opportunity_portfolio_eligible"] = row["opportunity_portfolio_eligible"]
        details[row["secid"]]["eligibility"]["opportunity_exclusion_codes"] = row["opportunity_exclusion_codes"]

    opportunities = build_opportunity_variants(rows, config)
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    compact_payload = {
        "schema_version": "4.0", "generated_at": generated_at,
        "source_universe_hash": _stable_hash(universe.get("bonds") or []),
        "as_of": universe.get("as_of") or {}, "bonds": rows,
    }
    counts = Counter(row["analysis_status"] for row in rows)
    structures = Counter(row["structure_class"] for row in rows)
    manifest = {
        "schema_version": "4.0", "generated_at": generated_at,
        "universe_hash": _stable_hash(rows), "detail_count": len(details),
        "detail_base_path": "bonds/details/", "details_lazy": True,
        "analysis_status": dict(sorted(counts.items())),
        "structures": dict(sorted(structures.items())),
        "curve": curve.metadata() if curve else {"status": "UNAVAILABLE"},
        "safe_portfolio_contract": "bonds/portfolio_presets.json",
        "opportunities_contract": "bonds/portfolio_opportunities.json",
        "oas_policy": "UNSUPPORTED_UNTIL_CALIBRATED_STOCHASTIC_OPTION_MODEL",
    }
    temp_details = Path(tempfile.mkdtemp(prefix="bond-v4-details-", dir=output_dir))
    try:
        for secid, detail in details.items():
            _atomic_json(temp_details / f"{secid}.json", detail)
        target_details = output_dir / "details"
        old_details = output_dir / ".details-old"
        if old_details.exists(): shutil.rmtree(old_details)
        if target_details.exists(): os.replace(target_details, old_details)
        os.replace(temp_details, target_details)
        if old_details.exists(): shutil.rmtree(old_details)
    finally:
        if temp_details.exists(): shutil.rmtree(temp_details)
    _atomic_json(output_dir / "universe_v4.json", compact_payload)
    _atomic_json(output_dir / "analytics_manifest.json", manifest)
    _atomic_json(output_dir / "portfolio_opportunities.json", opportunities)
    _atomic_json(output_dir / "portfolio_safe_v4.json", {
        "schema_version": "4.0", "mode": "safe", "generated_at": generated_at,
        "source_contract": "portfolio_presets.json", "semantics": "unchanged_v3_safe_allocator",
    })
    return manifest


def main() -> int:
    universe = json.loads((OUT / "universe.json").read_text(encoding="utf-8"))
    chart_path = OUT / "chart_data.json"
    curve_points = None
    if chart_path.exists():
        chart = json.loads(chart_path.read_text(encoding="utf-8"))
        curve_points = [(item["t"], item["yield"]) for item in chart.get("ofz_curve") or []]
    manifest = build_v4_artifacts(universe, curve_points=curve_points)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
