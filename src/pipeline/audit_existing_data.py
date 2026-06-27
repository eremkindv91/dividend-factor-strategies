from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from src.io_utils import utc_now_iso, write_empty_csv, write_json
from src.paths import REPO_ROOT, ensure_dir

SMARTLAB_CANDIDATES = [
    "results/smartlab/live_forecast.csv",
    "results/smartlab/reconcile.csv",
    "data/panels_final/panel_russia_final_smartlab.csv",
    "data/panels_final/panel_russia_final.csv",
]

SITE_JSON = [
    "site/data.json",
    "site/returns.json",
    "site/marketsaw.json",
    "site/marlamov.json",
    "site/methodology.json",
    "site/bonds/screener.json",
    "site/bonds/chart_data.json",
    "site/bonds/portfolios.json",
]

MANUAL_REVIEW_FILES = {
    "company_mapping_issues.csv": ["ticker", "company_name", "issue", "created_at"],
    "smartlab_migration_issues.csv": ["file_path", "ticker", "year", "field", "issue", "created_at"],
    "report_classification_issues.csv": ["report_id", "ticker", "document_title", "issue", "created_at"],
    "report_source_errors.csv": ["ticker", "source_type", "source_url", "issue", "detail", "created_at"],
    "extraction_low_confidence.csv": ["report_id", "ticker", "line_item_raw", "confidence_score", "created_at"],
    "financial_validation_errors.csv": ["ticker", "period", "line_item_std", "issue", "created_at"],
    "suspicious_outliers.csv": ["ticker", "period", "line_item_std", "value", "issue", "created_at"],
    "source_conflicts.csv": [
        "ticker", "company_name", "fiscal_year", "period", "line_item_std",
        "smartlab_value", "official_ifrs_value", "difference_abs", "difference_pct",
        "smartlab_source_url", "official_source_url", "suggested_value", "suggested_reason",
        "conflict_type", "created_at",
    ],
}


def audit_existing_data(root: Path = REPO_ROOT) -> dict:
    ensure_dir(root / "data" / "manual_review")
    for name, fields in MANUAL_REVIEW_FILES.items():
        path = root / "data" / "manual_review" / name
        if not path.exists():
            write_empty_csv(path, fields)

    smartlab = []
    for rel in SMARTLAB_CANDIDATES:
        path = root / rel
        item = {"path": rel, "exists": path.exists()}
        if path.exists():
            item["size"] = path.stat().st_size
            if path.suffix.lower() == ".csv":
                try:
                    df0 = pd.read_csv(path, nrows=0)
                    item["columns"] = list(df0.columns)
                except Exception as e:  # noqa: BLE001
                    item["read_error"] = str(e)
        smartlab.append(item)

    site = []
    for rel in SITE_JSON:
        path = root / rel
        item = {"path": rel, "exists": path.exists()}
        if path.exists():
            try:
                obj = json.loads(path.read_text(encoding="utf-8"))
                item["keys"] = list(obj.keys()) if isinstance(obj, dict) else []
                if isinstance(obj, dict) and "meta" in obj:
                    item["meta_keys"] = list((obj.get("meta") or {}).keys())
            except Exception as e:  # noqa: BLE001
                item["read_error"] = str(e)
        site.append(item)

    workflows = sorted(str(p.relative_to(root)) for p in (root / ".github" / "workflows").glob("*.yml"))
    tests = sorted(str(p.relative_to(root)) for p in root.glob("tests/test_*.py"))
    summary = {
        "run_date": utc_now_iso(),
        "smartlab_files": smartlab,
        "site_json": site,
        "workflows": workflows,
        "tests": tests,
    }
    write_json(root / "data" / "pipeline_summary.json", {"audit": summary})
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    args = parser.parse_args()
    summary = audit_existing_data(Path(args.repo_root))
    print("[audit] SmartLab files:")
    for item in summary["smartlab_files"]:
        print(f"  {item['path']}: {'OK' if item['exists'] else 'missing'}")
    print(f"[audit] workflows={len(summary['workflows'])}, tests={len(summary['tests'])}")
    print("[audit] wrote data/pipeline_summary.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
