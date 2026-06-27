from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.io_utils import write_parquet
from src.paths import REPO_ROOT


REPORT_COLUMNS = [
    "report_id", "ticker", "inn", "company_name", "period", "period_type", "fiscal_year",
    "publication_date", "document_title", "document_type", "reporting_standard", "file_path",
    "source_url", "source_name", "file_hash", "download_status", "parse_status",
    "extraction_status", "quality_status", "created_at", "updated_at",
]


def fetch_reports(root: Path = REPO_ROOT, *_args, **_kwargs) -> dict:
    path = root / "data" / "report_index.parquet"
    if not path.exists():
        write_parquet(path, pd.DataFrame(columns=REPORT_COLUMNS))
    return {"reports_found": 0, "reports_downloaded": 0, "mode": "disabled_safe_baseline"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--from-year", type=int)
    parser.add_argument("--to-year", type=int)
    parser.add_argument("--ticker")
    args = parser.parse_args()
    res = fetch_reports(Path(args.repo_root), from_year=args.from_year, to_year=args.to_year, ticker=args.ticker)
    print(f"[fetch-reports] reports_downloaded={res['reports_downloaded']} mode={res['mode']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

