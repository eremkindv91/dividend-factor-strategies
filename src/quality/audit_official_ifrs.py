from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.io_utils import write_csv, write_json
from src.paths import REPO_ROOT


AUDIT_COLUMNS = [
    "ticker",
    "company_name",
    "fiscal_year",
    "period",
    "year_status",
    "metric",
    "line_item_std",
    "currency",
    "official_ifrs_value",
    "smartlab_value",
    "selected_value",
    "source_status",
    "conflict_flag",
    "needs_manual_review",
    "selected_reason",
    "official_fact_role",
    "source_url",
    "quality_score",
]


def build_official_ifrs_audit(root: Path = REPO_ROOT) -> pd.DataFrame:
    seed_path = root / "data" / "official_ifrs_facts.csv"
    unified_path = root / "data" / "unified" / "financial_facts_unified.parquet"
    if not seed_path.exists():
        return pd.DataFrame(columns=AUDIT_COLUMNS)
    seed = pd.read_csv(seed_path)
    if seed.empty:
        return pd.DataFrame(columns=AUDIT_COLUMNS)

    keys = ["ticker", "fiscal_year", "period", "currency", "line_item_std"]
    seed = seed.copy()
    for col in keys:
        if col not in seed:
            seed[col] = None
    if "year_status" not in seed:
        seed["year_status"] = seed["fiscal_year"].map(_year_status)
    seed["ticker"] = seed["ticker"].astype(str).str.upper().str.strip()
    seed["period"] = seed["period"].astype(str)
    seed["line_item_std"] = seed["line_item_std"].astype(str)
    seed["currency"] = seed["currency"].astype(str)
    seed["fiscal_year"] = pd.to_numeric(seed["fiscal_year"], errors="coerce").astype("Int64")

    if unified_path.exists():
        unified = pd.read_parquet(unified_path)
    else:
        unified = pd.DataFrame()
    if unified.empty:
        merged = seed.assign(
            best_value=pd.NA,
            best_source_name=pd.NA,
            best_quality_score=pd.NA,
            smartlab_value=pd.NA,
            official_ifrs_value=pd.NA,
            official_ifrs_source_url=pd.NA,
            selected_reason=pd.NA,
            conflict_flag=False,
            needs_manual_review=False,
        )
    else:
        unified = unified.copy()
        for col in keys:
            if col not in unified:
                unified[col] = None
        unified["ticker"] = unified["ticker"].astype(str).str.upper().str.strip()
        unified["period"] = unified["period"].astype(str)
        unified["line_item_std"] = unified["line_item_std"].astype(str)
        unified["currency"] = unified["currency"].astype(str)
        unified["fiscal_year"] = pd.to_numeric(unified["fiscal_year"], errors="coerce").astype("Int64")
        keep = keys + [
            "best_value",
            "best_source_name",
            "best_quality_score",
            "smartlab_value",
            "official_ifrs_value",
            "official_ifrs_source_url",
            "selected_reason",
            "conflict_flag",
            "needs_manual_review",
        ]
        for col in keep:
            if col not in unified:
                unified[col] = None
        merged = seed.merge(unified[keep], on=keys, how="left")

    rows = []
    for _, row in merged.iterrows():
        official_value = _coalesce(row.get("official_ifrs_value"), row.get("value_normalized"))
        selected_value = row.get("best_value")
        source_status = _source_status(row)
        source_url = _coalesce(row.get("official_ifrs_source_url"), row.get("source_url"))
        rows.append({
            "ticker": row.get("ticker"),
            "company_name": row.get("company_name"),
            "fiscal_year": _int_or_none(row.get("fiscal_year")),
            "period": str(row.get("period") or ""),
            "year_status": _coalesce(row.get("year_status"), _year_status(row.get("fiscal_year"))),
            "metric": row.get("line_item_std"),
            "line_item_std": row.get("line_item_std"),
            "currency": row.get("currency"),
            "official_ifrs_value": _num_or_none(official_value),
            "smartlab_value": _num_or_none(row.get("smartlab_value")),
            "selected_value": _num_or_none(selected_value),
            "source_status": source_status,
            "conflict_flag": _bool(row.get("conflict_flag")),
            "needs_manual_review": _bool(row.get("needs_manual_review")),
            "selected_reason": None if pd.isna(row.get("selected_reason")) else str(row.get("selected_reason")),
            "official_fact_role": _official_fact_role(row, official_value, selected_value),
            "source_url": None if pd.isna(source_url) else str(source_url),
            "quality_score": _num_or_none(_coalesce(row.get("best_quality_score"), row.get("quality_score"))),
        })
    return pd.DataFrame(rows, columns=AUDIT_COLUMNS)


def write_official_ifrs_audit(root: Path = REPO_ROOT) -> dict:
    audit = build_official_ifrs_audit(root)
    rows = audit.to_dict(orient="records")
    write_csv(root / "data" / "quality" / "official_ifrs_audit.csv", rows, AUDIT_COLUMNS)
    summary = official_ifrs_audit_summary(audit)
    write_json(root / "data" / "unified" / "official_ifrs_audit.json", {"meta": summary, "rows": rows})
    return summary


def official_ifrs_audit_summary(audit: pd.DataFrame) -> dict:
    if audit.empty:
        return {
            "official_ifrs_facts": 0,
            "missing_official_facts": 0,
            "role_counts": {},
            "source_status_counts": {},
            "year_status_counts": {},
        }
    return {
        "official_ifrs_facts": int(len(audit)),
        "missing_official_facts": int((audit["official_fact_role"] == "missing").sum()),
        "role_counts": _counts(audit["official_fact_role"]),
        "source_status_counts": _counts(audit["source_status"]),
        "year_status_counts": _counts(audit["year_status"]),
    }


def _counts(series: pd.Series) -> dict:
    return {str(k): int(v) for k, v in series.fillna("Missing").value_counts().to_dict().items()}


def _source_status(row: pd.Series) -> str:
    if pd.isna(row.get("best_value")):
        return "Missing"
    if _bool(row.get("conflict_flag")):
        return "Conflict"
    if _bool(row.get("needs_manual_review")):
        return "Needs review"
    source = str(row.get("best_source_name") or "").lower()
    if "official_ifrs" in source:
        return "Official IFRS"
    if "smartlab" in source:
        return "SmartLab fallback"
    return "Needs review"


def _official_fact_role(row: pd.Series, official_value, selected_value) -> str:
    if pd.isna(row.get("best_value")):
        return "missing"
    source = str(row.get("best_source_name") or "").lower()
    if "official_ifrs" in source and _values_close(official_value, selected_value):
        return "best_value"
    if not pd.isna(row.get("official_ifrs_value")):
        return "comparison_source"
    return "missing"


def _year_status(fiscal_year) -> str:
    year = _int_or_none(fiscal_year)
    if year is None:
        return "unknown"
    if year == 2024:
        return "current_2024"
    if year < 2024:
        return "stale"
    return "newer_than_target"


def _values_close(a, b) -> bool:
    av = _num_or_none(a)
    bv = _num_or_none(b)
    if av is None or bv is None:
        return False
    return abs(av - bv) <= max(1.0, abs(av)) * 1e-9


def _bool(v) -> bool:
    if pd.isna(v):
        return False
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "y"}
    return bool(v)


def _coalesce(*values):
    for value in values:
        if not pd.isna(value):
            return value
    return None


def _num_or_none(v):
    if pd.isna(v):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int_or_none(v):
    if pd.isna(v):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    args = parser.parse_args()
    summary = write_official_ifrs_audit(Path(args.repo_root))
    print(
        "[official-ifrs-audit] "
        f"facts={summary['official_ifrs_facts']} missing={summary['missing_official_facts']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
