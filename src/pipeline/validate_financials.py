from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.io_utils import write_csv
from src.paths import REPO_ROOT
from src.quality.financial_validators import validate_non_negative


def validate_financials(root: Path = REPO_ROOT) -> dict:
    path = root / "data" / "processed" / "smartlab_fundamentals.parquet"
    errors = []
    if path.exists():
        df = pd.read_parquet(path)
        for _, row in df.iterrows():
            issue = validate_non_negative(str(row.get("line_item_std")), row.get("value_normalized"))
            if issue:
                errors.append({
                    "ticker": row.get("ticker"),
                    "period": row.get("period"),
                    "line_item_std": row.get("line_item_std"),
                    "issue": issue,
                    "created_at": row.get("created_at"),
                })
    unified_path = root / "data" / "unified" / "financial_facts_unified.parquet"
    if unified_path.exists():
        udf = pd.read_parquet(unified_path)
        for _, row in udf.iterrows():
            if not bool(row.get("is_reliable")):
                continue
            for field in ("best_source_name", "best_source_url", "period", "unit_multiplier"):
                if _is_missing(row.get(field)):
                    errors.append({
                        "ticker": row.get("ticker"),
                        "period": row.get("period"),
                        "line_item_std": row.get("line_item_std"),
                        "issue": f"missing_reliable_field:{field}",
                        "created_at": row.get("created_at"),
                    })
            unit_type = row.get("unit_type")
            if unit_type in {"currency", "per_share"} and _is_missing(row.get("currency")):
                errors.append({
                    "ticker": row.get("ticker"),
                    "period": row.get("period"),
                    "line_item_std": row.get("line_item_std"),
                    "issue": "missing_reliable_field:currency",
                    "created_at": row.get("created_at"),
                })
    write_csv(root / "data" / "manual_review" / "financial_validation_errors.csv", errors, ["ticker", "period", "line_item_std", "issue", "created_at"])
    return {"errors": len(errors)}


def _is_missing(value) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except TypeError:
        pass
    return str(value).strip() == ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    args = parser.parse_args()
    res = validate_financials(Path(args.repo_root))
    print(f"[validate-financials] errors={res['errors']}")
    return 0 if res["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
