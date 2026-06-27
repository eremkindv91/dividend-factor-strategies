from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.io_utils import utc_now_iso, write_json
from src.paths import REPO_ROOT, ensure_dir
from src.pipeline.audit_existing_data import audit_existing_data
from src.pipeline.build_site_data import build_site_data
from src.pipeline.migrate_existing_smartlab_data import migrate_smartlab
from src.pipeline.unify_financial_data import unify_financial_data


def run_all(root: Path = REPO_ROOT, smartlab_only: bool = False, skip_ocr: bool = True, **kwargs) -> dict:
    start = utc_now_iso()
    ensure_dir(root / "logs")
    audit = audit_existing_data(root)
    migration = migrate_smartlab(root, make_backup=True)
    skipped = []
    if smartlab_only:
        skipped += ["discover_companies", "fetch_reports", "extract_financials", "validate_financials"]
    elif skip_ocr:
        skipped.append("ocr")
    unify = unify_financial_data(root)
    site = build_site_data(root)
    registry_counts = _registry_counts(root)
    avg_quality = _average_quality(root)
    summary = {
        "run_date": start,
        "mode": "smartlab_only" if smartlab_only else "default",
        "skip_ocr": skip_ocr,
        "skipped_steps": skipped,
        "companies_total": migration["registry"]["companies"],
        "companies_active": registry_counts.get("active", 0),
        "companies_smartlab_only": registry_counts.get("smartlab_only", 0),
        "companies_ifrs_confirmed": registry_counts.get("ifrs_confirmed", 0),
        "companies_partial": registry_counts.get("partial", 0),
        "new_companies_added": None,
        "reports_found": 0,
        "reports_downloaded": 0,
        "reports_extracted": 0,
        "reports_failed": 0,
        "ocr_pages_processed": 0,
        "facts_from_smartlab": migration["facts_written"],
        "facts_from_ifrs": 0,
        "facts_from_manual_override": 0,
        "conflicts_count": unify["conflicts_count"],
        "duplicates_removed": unify["duplicates_removed"],
        "manual_review_count": migration["issues"] + unify["conflicts_count"],
        "average_quality_score": avg_quality,
        "estimated_ocr_cost": 0,
        "last_successful_run": utc_now_iso(),
        "audit": {"smartlab_files": len(audit["smartlab_files"])},
        "site_rows": site["rows"],
        "args": kwargs,
    }
    write_json(root / "data" / "pipeline_summary.json", summary)
    log_path = root / "logs" / f"pipeline_run_{start[:10]}.log"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(
            f"{utc_now_iso()} run_all mode={summary['mode']} "
            f"companies={summary['companies_total']} smartlab_only={summary['companies_smartlab_only']} "
            f"reports_found={summary['reports_found']} reports_downloaded={summary['reports_downloaded']} "
            f"reports_extracted={summary['reports_extracted']} ocr_pages={summary['ocr_pages_processed']} "
            f"smartlab_facts={summary['facts_from_smartlab']} ifrs_facts={summary['facts_from_ifrs']} "
            f"conflicts={summary['conflicts_count']} duplicates_removed={summary['duplicates_removed']} "
            f"manual_review={summary['manual_review_count']} estimated_ocr_cost={summary['estimated_ocr_cost']}\n"
        )
    return summary


def _registry_counts(root: Path) -> dict[str, int]:
    reg_path = root / "data" / "companies_registry.csv"
    if not reg_path.exists():
        return {}
    reg = pd.read_csv(reg_path, dtype=str).fillna("")
    if "coverage_status" not in reg:
        return {}
    return {str(k): int(v) for k, v in reg["coverage_status"].value_counts().to_dict().items()}


def _average_quality(root: Path) -> float | None:
    facts_path = root / "data" / "unified" / "financial_facts_unified.parquet"
    if not facts_path.exists():
        return None
    facts = pd.read_parquet(facts_path, columns=["best_quality_score"])
    if facts.empty:
        return None
    return round(float(facts["best_quality_score"].dropna().mean()), 2)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--limit-companies", type=int)
    parser.add_argument("--ticker")
    parser.add_argument("--from-year", type=int)
    parser.add_argument("--to-year", type=int)
    parser.add_argument("--skip-ocr", action="store_true")
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--manual-review-only", action="store_true")
    parser.add_argument("--smartlab-only", action="store_true")
    parser.add_argument("--ifrs-only", action="store_true")
    args = parser.parse_args()
    if args.dry_run:
        print("[run-all] dry-run: no writes beyond argument validation")
        return 0
    summary = run_all(
        Path(args.repo_root),
        smartlab_only=args.smartlab_only or not args.ifrs_only,
        skip_ocr=args.skip_ocr,
        limit_companies=args.limit_companies,
        ticker=args.ticker,
        from_year=args.from_year,
        to_year=args.to_year,
        force_refresh=args.force_refresh,
        manual_review_only=args.manual_review_only,
        ifrs_only=args.ifrs_only,
    )
    print(f"[run-all] mode={summary['mode']} smartlab_facts={summary['facts_from_smartlab']} site_rows={summary['site_rows']}")
    print("[run-all] wrote data/pipeline_summary.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
