from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.io_utils import write_parquet
from src.paths import REPO_ROOT


IFRS_COLUMNS = [
    "fact_id", "report_id", "ticker", "inn", "company_name", "fiscal_year", "period",
    "period_type", "reporting_standard", "statement_type", "line_item_raw", "line_item_std",
    "value_raw", "value_normalized", "currency", "unit_multiplier", "is_calculated", "formula",
    "source_page", "source_table_id", "source_url", "extraction_method", "confidence_score",
    "validation_status", "created_at",
]


def extract_financials(root: Path = REPO_ROOT, skip_ocr: bool = True) -> dict:
    path = root / "data" / "processed" / "ifrs_financial_facts.parquet"
    if not path.exists():
        write_parquet(path, pd.DataFrame(columns=IFRS_COLUMNS))
    return {"reports_extracted": 0, "ocr_skipped": skip_ocr, "mode": "no_reports_in_safe_baseline"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--skip-ocr", action="store_true")
    args = parser.parse_args()
    res = extract_financials(Path(args.repo_root), skip_ocr=args.skip_ocr)
    print(f"[extract] reports_extracted={res['reports_extracted']} ocr_skipped={res['ocr_skipped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

