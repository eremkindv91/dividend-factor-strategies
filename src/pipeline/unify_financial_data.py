from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.data_sources.manual_adapter import read_manual_override
from src.io_utils import stable_id, utc_now_iso, write_csv, write_json, write_parquet
from src.paths import REPO_ROOT
from src.unification.deduplication import FACT_DEDUP_FIELDS, fact_dedup_key
from src.unification.source_resolver import SourceCandidate, relative_diff, resolve_best_value


def unify_financial_data(root: Path = REPO_ROOT) -> dict:
    sources = []
    smartlab_path = root / "data" / "processed" / "smartlab_fundamentals.parquet"
    if smartlab_path.exists():
        smartlab = pd.read_parquet(smartlab_path)
        if not smartlab.empty:
            sources.append(smartlab)
    ifrs_path = root / "data" / "processed" / "ifrs_financial_facts.parquet"
    if ifrs_path.exists():
        ifrs = pd.read_parquet(ifrs_path)
        if not ifrs.empty:
            sources.append(ifrs)
    manual_path = root / "data" / "manual_overrides" / "financial_facts.csv"
    manual = read_manual_override(manual_path)
    if not manual.empty:
        sources.append(manual)
    if not sources:
        raise FileNotFoundError("No processed financial facts found. Run migrate_existing_smartlab_data first.")

    facts = pd.concat(sources, ignore_index=True, sort=False)
    facts, duplicates_removed = deduplicate_source_rows(facts)
    facts = facts.copy()
    facts["_resolution_standard"] = facts.apply(resolution_standard, axis=1)
    now = utc_now_iso()
    unified_rows = []
    conflicts = []

    group_fields = [
        "ticker",
        "fiscal_year",
        "period",
        "_resolution_standard",
        "statement_type",
        "line_item_std",
        "currency",
    ]
    for _, grp in facts.groupby(group_fields, dropna=False):
        candidates = [SourceCandidate.from_mapping(row.to_dict()) for _, row in grp.iterrows()]
        resolved = resolve_best_value(candidates)
        best = resolved["best"]
        first = grp.iloc[0].to_dict()
        smart = next((c for c in candidates if c.source_name == "smartlab" or c.source_type == "aggregator"), None)
        official = next((c for c in candidates if c.source_name.startswith("official_ifrs") or c.source_type == "official_ifrs"), None)
        manual = next((c for c in candidates if c.source_name == "manual_override" or c.source_type == "manual_override"), None)
        unified_id = stable_id("unified", fact_dedup_key(first))
        unit_type = best.unit_type if best else first.get("unit_type")
        currency = best.currency if best else first.get("currency")
        unit_multiplier = best.unit_multiplier if best else first.get("unit_multiplier")
        row = {
            "unified_fact_id": unified_id,
            "ticker": first.get("ticker"),
            "inn": first.get("inn"),
            "company_name": first.get("company_name"),
            "fiscal_year": first.get("fiscal_year"),
            "period": None if first.get("period") is None else str(first.get("period")),
            "reporting_standard": first.get("_resolution_standard"),
            "statement_type": first.get("statement_type"),
            "line_item_std": first.get("line_item_std"),
            "currency": currency,
            "unit_type": unit_type,
            "unit_multiplier": unit_multiplier,
            "best_value": best.value if best else None,
            "best_source_name": best.source_name if best else None,
            "best_source_type": best.source_type if best else None,
            "best_source_url": best.source_url if best else None,
            "best_quality_score": best.quality_score if best else None,
            "best_confidence_score": best.confidence_score if best else None,
            "smartlab_value": smart.value if smart else None,
            "smartlab_source_url": smart.source_url if smart else None,
            "official_ifrs_value": official.value if official else None,
            "official_ifrs_source_url": official.source_url if official else None,
            "manual_override_value": manual.value if manual else None,
            "selected_reason": resolved["selected_reason"],
            "conflict_flag": bool(resolved["conflict_flag"]),
            "conflict_type": resolved["conflict_type"],
            "needs_manual_review": bool(resolved["needs_manual_review"]),
            "is_reliable": bool(best and not resolved["needs_manual_review"] and is_reliable_candidate(best)),
            "created_at": now,
            "updated_at": now,
        }
        unified_rows.append(row)
        if row["conflict_flag"] or row["needs_manual_review"]:
            diff_abs, diff_pct = source_diff(smart, official)
            conflicts.append({
                "ticker": row["ticker"],
                "company_name": row["company_name"],
                "fiscal_year": row["fiscal_year"],
                "period": row["period"],
                "line_item_std": row["line_item_std"],
                "smartlab_value": row["smartlab_value"],
                "official_ifrs_value": row["official_ifrs_value"],
                "difference_abs": diff_abs,
                "difference_pct": diff_pct,
                "smartlab_source_url": row["smartlab_source_url"],
                "official_source_url": row["official_ifrs_source_url"],
                "suggested_value": row["best_value"],
                "suggested_reason": row["selected_reason"],
                "conflict_type": row["conflict_type"],
                "created_at": now,
            })

    unified = pd.DataFrame(unified_rows)
    write_parquet(root / "data" / "unified" / "financial_facts_unified.parquet", unified)
    wide = build_wide(unified)
    write_parquet(root / "data" / "unified" / "company_financials_unified.parquet", wide)
    write_csv(
        root / "data" / "manual_review" / "source_conflicts.csv",
        conflicts,
        [
            "ticker", "company_name", "fiscal_year", "period", "line_item_std",
            "smartlab_value", "official_ifrs_value", "difference_abs", "difference_pct",
            "smartlab_source_url", "official_source_url", "suggested_value", "suggested_reason",
            "conflict_type", "created_at",
        ],
    )
    summary = {
        "run_date": now,
        "facts_input": int(len(facts)),
        "facts_unified": int(len(unified)),
        "wide_rows": int(len(wide)),
        "duplicates_removed": int(duplicates_removed),
        "conflicts_count": len(conflicts),
        "reliable_facts": int(unified["is_reliable"].sum()) if "is_reliable" in unified else 0,
    }
    write_json(root / "data" / "pipeline_summary.json", {"unify": summary})
    return summary


def deduplicate_source_rows(facts: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Drop exact repeated source rows while preserving competing sources.

    The source resolver needs to see SmartLab and official IFRS candidates side
    by side. Deduplication here removes repeated loads of the same source value,
    not alternatives from different sources.
    """
    if facts.empty:
        return facts.copy(), 0
    candidate_cols = FACT_DEDUP_FIELDS + [
        "source_name",
        "source_type",
        "source_url",
        "value_normalized",
        "quality_score",
        "confidence_score",
    ]
    subset = [c for c in candidate_cols if c in facts.columns]
    out = facts.drop_duplicates(subset=subset, keep="first").reset_index(drop=True)
    return out, len(facts) - len(out)


def resolution_standard(row: pd.Series) -> str:
    standard = str(row.get("reporting_standard") or "").strip().upper()
    source_type = str(row.get("source_type") or "").strip().lower()
    source_name = str(row.get("source_name") or "").strip().lower()
    if standard in {"RAS", "РСБУ"}:
        return "RAS"
    if standard in {"IFRS", "МСФО"}:
        return "IFRS"
    if source_type == "aggregator" or source_name == "smartlab":
        return "IFRS"
    return standard or "UNKNOWN"


def build_wide(unified: pd.DataFrame) -> pd.DataFrame:
    if unified.empty:
        return pd.DataFrame()
    base_cols = ["ticker", "fiscal_year", "period"]
    piv = unified.pivot_table(index=base_cols, columns="line_item_std", values="best_value", aggfunc="first").reset_index()
    piv.columns.name = None
    quality = unified.groupby(base_cols, dropna=False)["best_quality_score"].mean().reset_index(name="quality_score")
    source = unified.groupby(base_cols, dropna=False)["best_source_name"].agg(lambda s: ",".join(sorted(set(str(x) for x in s if pd.notna(x))))).reset_index(name="source")
    conflicts = unified.groupby(base_cols, dropna=False)["conflict_flag"].any().reset_index(name="conflict_flag")
    out = piv.merge(quality, on=base_cols, how="left").merge(source, on=base_cols, how="left").merge(conflicts, on=base_cols, how="left")
    return out


def is_reliable_candidate(candidate: SourceCandidate) -> bool:
    if not candidate.source_name or not candidate.source_url or not candidate.period:
        return False
    if candidate.unit_multiplier in (None, ""):
        return False
    if candidate.unit_type in {"currency", "per_share"} and not candidate.currency:
        return False
    return candidate.quality_score >= 70


def source_diff(smart: SourceCandidate | None, official: SourceCandidate | None) -> tuple[float | None, float | None]:
    if not smart or not official or smart.value is None or official.value is None:
        return None, None
    diff_abs = abs(float(official.value) - float(smart.value))
    diff_pct = relative_diff(float(official.value), float(smart.value)) * 100
    return round(diff_abs, 6), round(diff_pct, 6)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    args = parser.parse_args()
    res = unify_financial_data(Path(args.repo_root))
    print(f"[unify] facts={res['facts_unified']} wide_rows={res['wide_rows']} conflicts={res['conflicts_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
