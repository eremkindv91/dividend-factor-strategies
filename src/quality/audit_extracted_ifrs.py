from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.io_utils import utc_now_iso, write_csv, write_json
from src.normalization.tickers import canonical_ticker
from src.paths import REPO_ROOT


AUDIT_COLUMNS = [
    "ticker",
    "company_name",
    "fiscal_year",
    "period",
    "metric",
    "line_item_std",
    "currency",
    "extracted_value",
    "verified_seed_value",
    "smartlab_value",
    "selected_value",
    "diff_vs_seed_pct",
    "diff_vs_smartlab_pct",
    "source_name",
    "extraction_method",
    "report_id",
    "source_table_id",
    "source_url",
    "quality_score",
    "confidence_score",
    "audit_status",
    "needs_manual_review",
    "audit_reason",
]


def build_extracted_ifrs_audit(root: Path = REPO_ROOT) -> pd.DataFrame:
    processed_path = root / "data" / "processed" / "ifrs_financial_facts.parquet"
    if not processed_path.exists():
        return pd.DataFrame(columns=AUDIT_COLUMNS)
    facts = pd.read_parquet(processed_path)
    if facts.empty:
        return pd.DataFrame(columns=AUDIT_COLUMNS)

    facts = facts.copy()
    facts["source_name"] = facts.get("source_name", pd.Series(dtype=str)).fillna("").astype(str)
    extracted = facts[facts["source_name"] != "official_ifrs_verified_seed"].copy()
    if extracted.empty:
        return pd.DataFrame(columns=AUDIT_COLUMNS)

    seed = load_seed_lookup(root)
    unified = load_unified_lookup(root)
    rows: list[dict] = []
    for _, fact in extracted.iterrows():
        key = fact_key(fact)
        seed_row = seed.get(key)
        unified_row = unified.get(key)
        extracted_value = _num_or_none(fact.get("value_normalized"))
        seed_value = _num_or_none(seed_row.get("value_normalized")) if seed_row else None
        smartlab_value = _num_or_none(unified_row.get("smartlab_value")) if unified_row else None
        selected_value = _num_or_none(unified_row.get("best_value")) if unified_row else None
        status, needs_review, reason = classify_extracted_fact(extracted_value, seed_value, smartlab_value)
        rows.append({
            "ticker": canonical_ticker(fact.get("ticker"), root),
            "company_name": fact.get("company_name"),
            "fiscal_year": _int_or_none(fact.get("fiscal_year")),
            "period": str(fact.get("period") or ""),
            "metric": fact.get("line_item_std"),
            "line_item_std": fact.get("line_item_std"),
            "currency": fact.get("currency"),
            "extracted_value": extracted_value,
            "verified_seed_value": seed_value,
            "smartlab_value": smartlab_value,
            "selected_value": selected_value,
            "diff_vs_seed_pct": relative_diff_pct(extracted_value, seed_value),
            "diff_vs_smartlab_pct": relative_diff_pct(extracted_value, smartlab_value),
            "source_name": fact.get("source_name"),
            "extraction_method": fact.get("extraction_method"),
            "report_id": fact.get("report_id"),
            "source_table_id": fact.get("source_table_id"),
            "source_url": fact.get("source_url"),
            "quality_score": _num_or_none(fact.get("quality_score")),
            "confidence_score": _num_or_none(fact.get("confidence_score")),
            "audit_status": status,
            "needs_manual_review": needs_review,
            "audit_reason": reason,
        })
    out = pd.DataFrame(rows, columns=AUDIT_COLUMNS)
    if "needs_manual_review" in out:
        out["needs_manual_review"] = out["needs_manual_review"].astype(object)
    return out


def write_extracted_ifrs_audit(root: Path = REPO_ROOT) -> dict:
    audit = build_extracted_ifrs_audit(root)
    rows = audit.to_dict(orient="records")
    write_csv(root / "data" / "manual_review" / "extracted_ifrs_audit.csv", rows, AUDIT_COLUMNS)
    summary = extracted_ifrs_audit_summary(audit)
    write_json(root / "data" / "manual_review" / "extracted_ifrs_audit_summary.json", {"meta": summary, "rows": rows})
    return summary


def extracted_ifrs_audit_summary(audit: pd.DataFrame) -> dict:
    if audit.empty:
        return {
            "generated_at": utc_now_iso(),
            "extracted_ifrs_facts": 0,
            "needs_manual_review": 0,
            "status_counts": {},
            "source_counts": {},
            "companies": [],
        }
    return {
        "generated_at": utc_now_iso(),
        "extracted_ifrs_facts": int(len(audit)),
        "needs_manual_review": int(audit["needs_manual_review"].map(bool).sum()),
        "status_counts": _counts(audit["audit_status"]),
        "source_counts": _counts(audit["source_name"]),
        "companies": sorted(audit["ticker"].dropna().astype(str).str.upper().unique().tolist()),
    }


def classify_extracted_fact(extracted_value: float | None, seed_value: float | None, smartlab_value: float | None) -> tuple[str, bool, str]:
    if extracted_value is None:
        return "needs_review", True, "extracted value is missing"
    if seed_value is not None:
        diff = relative_diff(extracted_value, seed_value)
        if diff <= 0.01:
            return "matches_verified_seed", False, "extracted value is within 1% of verified seed"
        if diff <= 0.05:
            return "minor_diff_vs_verified_seed", True, "extracted value differs from verified seed by 1-5%"
        return "conflict_vs_verified_seed", True, "extracted value differs from verified seed by more than 5%"
    if smartlab_value is not None:
        diff = relative_diff(extracted_value, smartlab_value)
        if diff <= 0.01:
            return "confirmed_by_smartlab", False, "extracted value is within 1% of SmartLab"
        if diff <= 0.05:
            return "minor_diff_vs_smartlab", True, "extracted value differs from SmartLab by 1-5%"
        return "needs_review", True, "no verified seed and SmartLab difference is more than 5%"
    return "needs_review", True, "no verified seed or SmartLab comparator"


def load_seed_lookup(root: Path) -> dict[tuple, dict]:
    path = root / "data" / "official_ifrs_facts.csv"
    if not path.exists():
        return {}
    df = pd.read_csv(path)
    return {fact_key(row): row.to_dict() for _, row in df.iterrows()}


def load_unified_lookup(root: Path) -> dict[tuple, dict]:
    path = root / "data" / "unified" / "financial_facts_unified.parquet"
    if not path.exists():
        return {}
    df = pd.read_parquet(path)
    return {fact_key(row): row.to_dict() for _, row in df.iterrows()}


def fact_key(row) -> tuple:
    return (
        canonical_ticker(row.get("ticker")),
        _int_or_none(row.get("fiscal_year")),
        str(row.get("period") or ""),
        str(row.get("currency") or ""),
        str(row.get("line_item_std") or ""),
    )


def relative_diff_pct(a: float | None, b: float | None) -> float | None:
    diff = relative_diff(a, b)
    return None if diff is None else diff * 100


def relative_diff(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return abs(float(a) - float(b)) / max(abs(float(b)), 1.0)


def _counts(series: pd.Series) -> dict:
    return {str(k): int(v) for k, v in series.fillna("Missing").value_counts().to_dict().items()}


def _num_or_none(value) -> float | None:
    if pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value) -> int | None:
    if pd.isna(value):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    args = parser.parse_args()
    summary = write_extracted_ifrs_audit(Path(args.repo_root))
    print(
        "[extracted-ifrs-audit] "
        f"facts={summary['extracted_ifrs_facts']} review={summary['needs_manual_review']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
