from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.data_sources.disclosure_adapter import DISCLOSURE_ERROR_COLUMNS
from src.io_utils import utc_now_iso, write_csv, write_json
from src.paths import REPO_ROOT, ensure_dir
from src.pipeline.audit_existing_data import audit_existing_data
from src.pipeline.build_site_data import build_site_data
from src.pipeline.discover_companies import discover_companies, registry_count
from src.pipeline.download_audited_reports import download_audited_reports
from src.pipeline.extract_financials import extract_financials
from src.pipeline.fetch_reports import fetch_reports
from src.pipeline.migrate_existing_smartlab_data import migrate_smartlab
from src.pipeline.unify_financial_data import unify_financial_data
from src.pipeline.validate_financials import validate_financials
from src.quality.audit_disclosure_links import audit_report_index
from src.quality.audit_extracted_ifrs import write_extracted_ifrs_audit
from src.quality.audit_fundamental_values import write_smartlab_fundamentals_cleaned
from src.quality.audit_official_ifrs import write_official_ifrs_audit
from src.quality.audit_smartlab_outliers import write_smartlab_outlier_audit, write_smartlab_outlier_triage


def run_all(root: Path = REPO_ROOT, smartlab_only: bool = False, skip_ocr: bool = True, **kwargs) -> dict:
    start = utc_now_iso()
    ensure_dir(root / "logs")
    no_network = kwargs.get("no_network", False)
    audit = audit_existing_data(root)
    migration = migrate_smartlab(root, make_backup=True)
    smartlab_outliers = {
        "total_outliers": 0,
        "high_severity": 0,
        "medium_severity": 0,
        "by_issue": {},
        "by_field": {},
        "companies": [],
    }
    try:
        smartlab_outliers = write_smartlab_outlier_audit(root)
    except Exception as e:  # noqa: BLE001
        smartlab_outliers = {
            "total_outliers": 0,
            "high_severity": 0,
            "medium_severity": 0,
            "by_issue": {},
            "by_field": {},
            "companies": [],
            "error": str(e),
        }
    smartlab_outlier_triage = {
        "total_triage_rows": 0,
        "suspected_unit_mismatch": 0,
        "high_priority": 0,
        "batch_count": 0,
        "review_batches": 0,
        "review_probable_full_company_unit_mismatch": 0,
        "review_single_field_needs_source_check": 0,
        "review_candidate_rows": 0,
        "review_do_not_auto_correct": True,
        "override_candidate_rows": 0,
        "override_candidate_ready": 0,
        "override_candidate_companies": [],
        "by_status": {},
        "by_issue": {},
        "companies": [],
    }
    try:
        smartlab_outlier_triage = write_smartlab_outlier_triage(root)
    except Exception as e:  # noqa: BLE001
        smartlab_outlier_triage = {
            "total_triage_rows": 0,
            "suspected_unit_mismatch": 0,
            "high_priority": 0,
            "batch_count": 0,
            "review_batches": 0,
            "review_probable_full_company_unit_mismatch": 0,
            "review_single_field_needs_source_check": 0,
            "review_candidate_rows": 0,
            "review_do_not_auto_correct": True,
            "override_candidate_rows": 0,
            "override_candidate_ready": 0,
            "override_candidate_companies": [],
            "by_status": {},
            "by_issue": {},
            "companies": [],
            "error": str(e),
        }
    smartlab_cleaned = {
        "smartlab_fields_loaded": 0,
        "smartlab_companies_loaded": 0,
        "smartlab_company_year_rows": 0,
        "smartlab_fundamental_values_total": 0,
        "smartlab_fundamental_values_clean": 0,
        "smartlab_fundamental_values_excluded": 0,
        "smartlab_ratio_values_blocked": 0,
        "smartlab_values_needs_review": 0,
        "smartlab_missing_values": 0,
        "smartlab_corrected_confirmed": 0,
        "smartlab_backfilled_values": 0,
        "smartlab_calculated_values": 0,
        "smartlab_backfill_failed": 0,
        "smartlab_gap_total_expected_values": 0,
        "smartlab_gap_present_in_site": 0,
        "smartlab_gap_missing_in_site": 0,
        "smartlab_gap_missing_can_be_calculated": 0,
        "smartlab_gap_missing_needs_backfill": 0,
    }
    try:
        smartlab_cleaned = write_smartlab_fundamentals_cleaned(root)
    except Exception as e:  # noqa: BLE001
        smartlab_cleaned["smartlab_cleaned_layer_error"] = str(e)
    skipped = []
    discover = {"new_companies_added": None, "source_rows": 0}
    reports = {
        "reports_found": 0,
        "reports_found_from_disclosure": 0,
        "reports_downloaded": 0,
        "disclosure_errors_count": 0,
        "last_disclosure_check": None,
        "report_index_updated_at": None,
    }
    disclosure_audit = {}
    audited_download = {"reports_downloaded": 0, "download_failed": 0, "eligible_reports": 0}
    extraction = {"reports_extracted": 0, "reports_failed": 0, "facts_from_ifrs": 0}
    ifrs_failures = []
    if smartlab_only:
        skipped += ["discover_companies", "fetch_reports", "extract_financials", "validate_financials"]
    else:
        if skip_ocr:
            skipped.append("ocr")
        try:
            discover_kwargs = {"use_moex": kwargs.get("use_moex_discovery", not no_network)}
            if "moex_securities" in kwargs:
                discover_kwargs["moex_securities"] = kwargs["moex_securities"]
            discover = discover_companies(root, **discover_kwargs)
        except Exception as e:  # noqa: BLE001
            ifrs_failures.append({"step": "discover_companies", "error": str(e)})
        try:
            reports = fetch_reports(
                root,
                from_year=kwargs.get("from_year"),
                to_year=kwargs.get("to_year"),
                ticker=kwargs.get("ticker"),
                limit_companies=kwargs.get("limit_companies"),
                no_network=no_network,
                download=kwargs.get("download_reports", False),
            )
        except Exception as e:  # noqa: BLE001
            ifrs_failures.append({"step": "fetch_reports", "error": str(e)})
            reports = record_disclosure_failure(root, str(e))
        write_json(root / "data" / "disclosure_summary.json", disclosure_summary_from_reports(reports))
        if kwargs.get("download_audited_reports", False):
            try:
                disclosure_audit = audit_report_index(root)
            except Exception as e:  # noqa: BLE001
                ifrs_failures.append({"step": "audit_disclosure_links", "error": str(e)})
            else:
                try:
                    audited_download = download_audited_reports(root)
                    reports["reports_downloaded"] = audited_download.get("reports_downloaded", 0)
                except Exception as e:  # noqa: BLE001
                    ifrs_failures.append({"step": "download_audited_reports", "error": str(e)})
        try:
            extraction = extract_financials(root, skip_ocr=skip_ocr)
        except Exception as e:  # noqa: BLE001
            ifrs_failures.append({"step": "extract_financials", "error": str(e)})
    unify = unify_financial_data(root)
    extracted_ifrs_audit = {
        "extracted_ifrs_facts": 0,
        "needs_manual_review": 0,
        "status_counts": {},
        "source_counts": {},
        "companies": [],
    }
    try:
        extracted_ifrs_audit = write_extracted_ifrs_audit(root)
    except Exception as e:  # noqa: BLE001
        ifrs_failures.append({"step": "audit_extracted_ifrs", "error": str(e)})
    official_audit = write_official_ifrs_audit(root)
    validation = validate_financials(root)
    site = build_site_data(root)
    registry_counts = _registry_counts(root)
    registry_total = registry_count(root)
    avg_quality = _average_quality(root)
    summary = {
        "run_date": start,
        "mode": "smartlab_only" if smartlab_only else "default",
        "skip_ocr": skip_ocr,
        "skipped_steps": skipped,
        "companies_total": registry_total,
        "companies_active": registry_counts.get("active", 0),
        "companies_smartlab_only": registry_counts.get("smartlab_only", 0),
        "companies_ifrs_confirmed": registry_counts.get("ifrs_confirmed", 0),
        "companies_partial": registry_counts.get("partial", 0),
        "new_companies_added": discover.get("new_companies_added"),
        "source_rows": discover.get("source_rows", 0),
        "reports_found": reports.get("reports_found", 0),
        "reports_found_from_disclosure": reports.get("reports_found_from_disclosure", reports.get("reports_found", 0)),
        "reports_downloaded": reports.get("reports_downloaded", 0),
        "audited_reports_eligible": audited_download.get("eligible_reports", 0),
        "audited_reports_downloaded": audited_download.get("reports_downloaded", 0),
        "audited_download_failed": audited_download.get("download_failed", 0),
        "disclosure_audit_ok": disclosure_audit.get("ok"),
        "disclosure_audit_needs_review": disclosure_audit.get("needs_review"),
        "reports_extracted": extraction.get("reports_extracted", 0),
        "reports_failed": extraction.get("reports_failed", 0),
        "disclosure_errors_count": reports.get("disclosure_errors_count", reports.get("source_errors", 0)),
        "last_disclosure_check": reports.get("last_disclosure_check"),
        "report_index_updated_at": reports.get("report_index_updated_at"),
        "ifrs_failures": ifrs_failures,
        "validation_errors": validation.get("errors", 0),
        "ocr_pages_processed": 0,
        "facts_from_smartlab": migration["facts_written"],
        "smartlab_panel_outliers_count": smartlab_outliers.get("total_outliers", 0),
        "smartlab_panel_outliers_high": smartlab_outliers.get("high_severity", 0),
        "smartlab_panel_outliers_by_issue": smartlab_outliers.get("by_issue", {}),
        "smartlab_panel_outliers_by_field": smartlab_outliers.get("by_field", {}),
        "smartlab_panel_outliers_companies": smartlab_outliers.get("companies", []),
        "smartlab_panel_outliers_error": smartlab_outliers.get("error"),
        "smartlab_panel_triage_rows": smartlab_outlier_triage.get("total_triage_rows", 0),
        "smartlab_panel_triage_suspected_unit_mismatch": smartlab_outlier_triage.get("suspected_unit_mismatch", 0),
        "smartlab_panel_triage_high_priority": smartlab_outlier_triage.get("high_priority", 0),
        "smartlab_panel_triage_batch_count": smartlab_outlier_triage.get("batch_count", 0),
        "smartlab_panel_unit_mismatch_review_batches": smartlab_outlier_triage.get("review_batches", 0),
        "smartlab_panel_unit_mismatch_probable_full_company": smartlab_outlier_triage.get("review_probable_full_company_unit_mismatch", 0),
        "smartlab_panel_unit_mismatch_single_field": smartlab_outlier_triage.get("review_single_field_needs_source_check", 0),
        "smartlab_panel_unit_mismatch_candidate_rows": smartlab_outlier_triage.get("review_candidate_rows", 0),
        "smartlab_panel_unit_mismatch_do_not_auto_correct": smartlab_outlier_triage.get("review_do_not_auto_correct", True),
        "smartlab_panel_unit_mismatch_override_candidate_rows": smartlab_outlier_triage.get("override_candidate_rows", 0),
        "smartlab_panel_unit_mismatch_override_candidate_ready": smartlab_outlier_triage.get("override_candidate_ready", 0),
        "smartlab_panel_unit_mismatch_override_candidate_companies": smartlab_outlier_triage.get("override_candidate_companies", []),
        "smartlab_panel_triage_by_status": smartlab_outlier_triage.get("by_status", {}),
        "smartlab_panel_triage_error": smartlab_outlier_triage.get("error"),
        "smartlab_fields_loaded": smartlab_cleaned.get("smartlab_fields_loaded", 0),
        "smartlab_companies_loaded": smartlab_cleaned.get("smartlab_companies_loaded", 0),
        "smartlab_company_year_rows": smartlab_cleaned.get("smartlab_company_year_rows", 0),
        "smartlab_fundamental_values_total": smartlab_cleaned.get("smartlab_fundamental_values_total", 0),
        "smartlab_fundamental_values_clean": smartlab_cleaned.get("smartlab_fundamental_values_clean", 0),
        "smartlab_fundamental_values_excluded": smartlab_cleaned.get("smartlab_fundamental_values_excluded", 0),
        "smartlab_ratio_values_blocked": smartlab_cleaned.get("smartlab_ratio_values_blocked", 0),
        "smartlab_values_needs_review": smartlab_cleaned.get("smartlab_values_needs_review", 0),
        "smartlab_missing_values": smartlab_cleaned.get("smartlab_missing_values", 0),
        "smartlab_corrected_confirmed": smartlab_cleaned.get("smartlab_corrected_confirmed", 0),
        "smartlab_backfilled_values": smartlab_cleaned.get("smartlab_backfilled_values", 0),
        "smartlab_calculated_values": smartlab_cleaned.get("smartlab_calculated_values", 0),
        "smartlab_backfill_failed": smartlab_cleaned.get("smartlab_backfill_failed", 0),
        "smartlab_gap_total_expected_values": smartlab_cleaned.get("smartlab_gap_total_expected_values", 0),
        "smartlab_gap_present_in_site": smartlab_cleaned.get("smartlab_gap_present_in_site", 0),
        "smartlab_gap_missing_in_site": smartlab_cleaned.get("smartlab_gap_missing_in_site", 0),
        "smartlab_gap_missing_can_be_calculated": smartlab_cleaned.get("smartlab_gap_missing_can_be_calculated", 0),
        "smartlab_gap_missing_needs_backfill": smartlab_cleaned.get("smartlab_gap_missing_needs_backfill", 0),
        "smartlab_cleaned_layer_error": smartlab_cleaned.get("smartlab_cleaned_layer_error"),
        "facts_from_ifrs": extraction.get("facts_from_ifrs", 0),
        "extracted_ifrs_facts": extracted_ifrs_audit.get("extracted_ifrs_facts", 0),
        "extracted_ifrs_needs_review": extracted_ifrs_audit.get("needs_manual_review", 0),
        "extracted_ifrs_status_counts": extracted_ifrs_audit.get("status_counts", {}),
        "extracted_ifrs_source_counts": extracted_ifrs_audit.get("source_counts", {}),
        "extracted_ifrs_companies": extracted_ifrs_audit.get("companies", []),
        "official_ifrs_facts": official_audit["official_ifrs_facts"],
        "official_ifrs_missing_facts": official_audit["missing_official_facts"],
        "official_ifrs_role_counts": official_audit["role_counts"],
        "official_ifrs_status_counts": official_audit["source_status_counts"],
        "official_ifrs_year_status_counts": official_audit["year_status_counts"],
        "facts_from_manual_override": 0,
        "conflicts_count": unify["conflicts_count"],
        "duplicates_removed": unify["duplicates_removed"],
        "manual_review_count": (
            migration["issues"]
            + unify["conflicts_count"]
            + int(extracted_ifrs_audit.get("needs_manual_review", 0))
            + len(ifrs_failures)
            + validation.get("errors", 0)
        ),
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
            f"manual_review={summary['manual_review_count']} validation_errors={summary['validation_errors']} "
            f"ifrs_failures={len(ifrs_failures)} estimated_ocr_cost={summary['estimated_ocr_cost']}\n"
        )
    return summary


def record_disclosure_failure(root: Path, detail: str) -> dict:
    now = utc_now_iso()
    rows = [{
        "ticker": "",
        "inn": "",
        "company_name": "",
        "source_type": "pipeline",
        "source_url": "",
        "issue": "fetch_reports_exception",
        "detail": detail,
        "created_at": now,
    }]
    write_csv(root / "data" / "manual_review" / "disclosure_errors.csv", rows, DISCLOSURE_ERROR_COLUMNS)
    summary = {
        "reports_found": 0,
        "reports_found_from_disclosure": 0,
        "reports_downloaded": 0,
        "disclosure_errors_count": 1,
        "last_disclosure_check": now,
        "report_index_updated_at": None,
        "mode": "fetch_reports_exception",
    }
    write_json(root / "data" / "disclosure_summary.json", summary)
    return summary


def disclosure_summary_from_reports(reports: dict) -> dict:
    return {
        "reports_found": reports.get("reports_found", 0),
        "reports_found_from_disclosure": reports.get("reports_found_from_disclosure", reports.get("reports_found", 0)),
        "reports_downloaded": reports.get("reports_downloaded", 0),
        "disclosure_errors_count": reports.get("disclosure_errors_count", reports.get("source_errors", 0)),
        "last_disclosure_check": reports.get("last_disclosure_check"),
        "report_index_updated_at": reports.get("report_index_updated_at"),
        "mode": reports.get("mode", "run_all"),
    }


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
    parser.add_argument("--allow-network", action="store_true", help="Allow MOEX company discovery and report-page URL metadata/link discovery.")
    parser.add_argument("--download-reports", action="store_true", help="Download direct discovered report files into data/raw_reports.")
    parser.add_argument("--download-audited-reports", action="store_true", help="Audit disclosure links and download only audit_status=ok report files into data/raw_reports.")
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
        smartlab_only=args.smartlab_only,
        skip_ocr=args.skip_ocr,
        limit_companies=args.limit_companies,
        ticker=args.ticker,
        from_year=args.from_year,
        to_year=args.to_year,
        no_network=not args.allow_network,
        download_reports=args.download_reports,
        download_audited_reports=args.download_audited_reports,
        force_refresh=args.force_refresh,
        manual_review_only=args.manual_review_only,
        ifrs_only=args.ifrs_only,
    )
    print(f"[run-all] mode={summary['mode']} smartlab_facts={summary['facts_from_smartlab']} site_rows={summary['site_rows']}")
    print("[run-all] wrote data/pipeline_summary.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
