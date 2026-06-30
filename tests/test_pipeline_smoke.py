from pathlib import Path
import json

import pandas as pd

from src.pipeline.extract_financials import IFRS_COLUMNS
from src.pipeline.run_all import run_all


def test_run_all_smartlab_only_smoke(tmp_path: Path):
    panel_dir = tmp_path / "data" / "panels_final"
    panel_dir.mkdir(parents=True)
    pd.DataFrame([
        {
            "ticker": "TEST",
            "year": 2025,
            "sector": "IT",
            "revenue_mln": 10,
            "assets_mln": 25,
            "net_profit_mln": 2,
        }
    ]).to_csv(panel_dir / "panel_russia_final_smartlab.csv", index=False)

    summary = run_all(tmp_path, smartlab_only=True, skip_ocr=True)

    assert summary["facts_from_smartlab"] == 3
    assert summary["site_rows"] == 1
    assert (tmp_path / "data" / "unified" / "site_financials.json").exists()
    assert (tmp_path / "site" / "site_coverage.json").exists()


def test_run_all_falls_back_to_smartlab_if_ifrs_fetch_fails(tmp_path: Path, monkeypatch):
    panel_dir = tmp_path / "data" / "panels_final"
    panel_dir.mkdir(parents=True)
    pd.DataFrame([
        {
            "ticker": "TEST",
            "year": 2025,
            "sector": "IT",
            "revenue_mln": 10,
        }
    ]).to_csv(panel_dir / "panel_russia_final_smartlab.csv", index=False)

    def broken_fetch_reports(*_args, **_kwargs):
        raise RuntimeError("issuer site unavailable")

    monkeypatch.setattr("src.pipeline.run_all.fetch_reports", broken_fetch_reports)

    summary = run_all(tmp_path, smartlab_only=False, skip_ocr=True, no_network=True)

    assert summary["mode"] == "default"
    assert summary["facts_from_smartlab"] == 1
    assert summary["site_rows"] == 1
    assert summary["ifrs_failures"] == [{"step": "fetch_reports", "error": "issuer site unavailable"}]
    assert (tmp_path / "site" / "site_financials.json").exists()


def test_run_all_summary_uses_registry_after_discovery(tmp_path: Path, monkeypatch):
    panel_dir = tmp_path / "data" / "panels_final"
    panel_dir.mkdir(parents=True)
    pd.DataFrame([
        {
            "ticker": "TEST",
            "year": 2025,
            "sector": "IT",
            "revenue_mln": 10,
        }
    ]).to_csv(panel_dir / "panel_russia_final_smartlab.csv", index=False)

    def add_candidate(root: Path, **_kwargs):
        reg_path = root / "data" / "companies_registry.csv"
        registry = pd.read_csv(reg_path, dtype=str).fillna("")
        candidate = {col: "" for col in registry.columns}
        candidate.update({
            "ticker": "CAND",
            "secid": "CAND",
            "short_name": "Candidate",
            "is_public": "true",
            "is_traded": "true",
            "has_smartlab_data": "false",
            "status": "needs_mapping",
            "coverage_status": "candidate",
            "notes": "created_from_moex_discovery",
        })
        pd.concat([registry, pd.DataFrame([candidate])], ignore_index=True).to_csv(reg_path, index=False)
        return {"new_companies_added": 1, "source_rows": 0}

    monkeypatch.setattr("src.pipeline.run_all.discover_companies", add_candidate)

    summary = run_all(tmp_path, smartlab_only=False, skip_ocr=True, no_network=True)
    coverage = json.loads((tmp_path / "site" / "site_coverage.json").read_text())

    assert summary["companies_total"] == 2
    assert coverage["meta"]["companies_total"] == 2


def test_run_all_no_network_disables_moex_discovery(tmp_path: Path, monkeypatch):
    panel_dir = tmp_path / "data" / "panels_final"
    panel_dir.mkdir(parents=True)
    pd.DataFrame([
        {
            "ticker": "TEST",
            "year": 2025,
            "sector": "IT",
            "revenue_mln": 10,
        }
    ]).to_csv(panel_dir / "panel_russia_final_smartlab.csv", index=False)

    def assert_no_network_discovery(root: Path, *, use_moex: bool = True, **_kwargs):
        assert use_moex is False
        return {"new_companies_added": 0, "source_rows": 0}

    monkeypatch.setattr("src.pipeline.run_all.discover_companies", assert_no_network_discovery)

    summary = run_all(tmp_path, smartlab_only=False, skip_ocr=True, no_network=True)

    assert summary["mode"] == "default"
    assert summary["ifrs_failures"] == []


def test_run_all_summary_includes_official_ifrs_audit_counts(tmp_path: Path):
    data_dir = tmp_path / "data"
    panel_dir = data_dir / "panels_final"
    panel_dir.mkdir(parents=True)
    pd.DataFrame([
        {
            "ticker": "TEST",
            "year": 2025,
            "sector": "IT",
            "revenue_mln": 100,
        }
    ]).to_csv(panel_dir / "panel_russia_final_smartlab.csv", index=False)
    seed = {col: "" for col in IFRS_COLUMNS}
    seed.update({
        "fact_id": "seed-test-revenue",
        "report_id": "report-test",
        "ticker": "TEST",
        "company_name": "Test Co",
        "fiscal_year": 2025,
        "period": "2025",
        "period_type": "annual",
        "reporting_standard": "IFRS",
        "statement_type": "income_statement",
        "line_item_raw": "Revenue",
        "line_item_std": "revenue",
        "value_raw": 100000000.0,
        "value_normalized": 100000000.0,
        "currency": "RUB",
        "unit_type": "currency",
        "unit_multiplier": 1,
        "is_calculated": False,
        "source_url": "https://example.com/test.pdf",
        "source_name": "official_ifrs_verified_seed",
        "source_type": "official_ifrs",
        "source_priority": 2,
        "extraction_method": "verified_seed",
        "confidence_score": 0.98,
        "quality_score": 96,
        "validation_status": "verified_seed",
        "created_at": "2026-06-28T00:00:00+00:00",
    })
    pd.DataFrame([seed], columns=IFRS_COLUMNS).to_csv(data_dir / "official_ifrs_facts.csv", index=False)

    summary = run_all(tmp_path, smartlab_only=False, skip_ocr=True, no_network=True)
    published = json.loads((tmp_path / "site" / "site_financials.json").read_text(encoding="utf-8"))

    assert summary["facts_from_ifrs"] == 1
    assert summary["official_ifrs_facts"] == 1
    assert summary["official_ifrs_missing_facts"] == 0
    assert summary["official_ifrs_role_counts"] == {"best_value": 1}
    assert published["official_facts"][0]["selected_reason"] == "official_ifrs_confirmed_by_smartlab"


def test_run_all_summary_and_site_coverage_include_disclosure_counters(tmp_path: Path, monkeypatch):
    panel_dir = tmp_path / "data" / "panels_final"
    panel_dir.mkdir(parents=True)
    pd.DataFrame([
        {
            "ticker": "TEST",
            "year": 2025,
            "sector": "IT",
            "revenue_mln": 100,
        }
    ]).to_csv(panel_dir / "panel_russia_final_smartlab.csv", index=False)

    def fake_fetch_reports(*_args, **_kwargs):
        return {
            "reports_found": 2,
            "reports_found_from_disclosure": 2,
            "reports_downloaded": 0,
            "disclosure_errors_count": 1,
            "last_disclosure_check": "2026-06-29T00:00:00+00:00",
            "report_index_updated_at": "2026-06-29T00:00:01+00:00",
        }

    monkeypatch.setattr("src.pipeline.run_all.fetch_reports", fake_fetch_reports)

    summary = run_all(tmp_path, smartlab_only=False, skip_ocr=True, no_network=False)
    coverage = json.loads((tmp_path / "site" / "site_coverage.json").read_text())

    assert summary["reports_found_from_disclosure"] == 2
    assert summary["disclosure_errors_count"] == 1
    assert summary["last_disclosure_check"] == "2026-06-29T00:00:00+00:00"
    assert summary["report_index_updated_at"] == "2026-06-29T00:00:01+00:00"
    assert coverage["meta"]["reports_found_from_disclosure"] == 2
    assert coverage["meta"]["disclosure_errors_count"] == 1
    assert coverage["meta"]["last_disclosure_check"] == "2026-06-29T00:00:00+00:00"


def test_run_all_downloads_audited_reports_only_when_flag_enabled(tmp_path: Path, monkeypatch):
    panel_dir = tmp_path / "data" / "panels_final"
    panel_dir.mkdir(parents=True)
    pd.DataFrame([
        {"ticker": "TEST", "year": 2025, "sector": "IT", "revenue_mln": 100},
    ]).to_csv(panel_dir / "panel_russia_final_smartlab.csv", index=False)

    calls = []

    def fake_fetch_reports(*_args, **_kwargs):
        return {
            "reports_found": 1,
            "reports_found_from_disclosure": 1,
            "reports_downloaded": 0,
            "disclosure_errors_count": 0,
            "last_disclosure_check": "2026-06-29T00:00:00+00:00",
            "report_index_updated_at": "2026-06-29T00:00:00+00:00",
        }

    def fake_audit_report_index(*_args, **_kwargs):
        calls.append("audit")
        return {"total_links": 1, "ok": 1, "blocked": 0, "failed": 0, "rejected": 0, "needs_review": 0}

    def fake_download_audited_reports(*_args, **_kwargs):
        calls.append("download")
        return {
            "eligible_reports": 1,
            "reports_downloaded": 1,
            "download_failed": 0,
            "skipped_not_ok": 0,
            "skipped_non_file_ok": 0,
            "errors": [],
        }

    monkeypatch.setattr("src.pipeline.run_all.fetch_reports", fake_fetch_reports)
    monkeypatch.setattr("src.pipeline.run_all.audit_report_index", fake_audit_report_index)
    monkeypatch.setattr("src.pipeline.run_all.download_audited_reports", fake_download_audited_reports)

    summary = run_all(tmp_path, smartlab_only=False, skip_ocr=True, no_network=True, download_audited_reports=True)

    assert calls == ["audit", "download"]
    assert summary["reports_downloaded"] == 1
    assert summary["audited_reports_downloaded"] == 1
    assert summary["audited_download_failed"] == 0


def test_run_all_summary_includes_extracted_ifrs_review_counts(tmp_path: Path, monkeypatch):
    panel_dir = tmp_path / "data" / "panels_final"
    panel_dir.mkdir(parents=True)
    pd.DataFrame([
        {"ticker": "TEST", "year": 2025, "sector": "IT", "revenue_mln": 100},
    ]).to_csv(panel_dir / "panel_russia_final_smartlab.csv", index=False)

    def fake_extracted_ifrs_audit(*_args, **_kwargs):
        return {
            "extracted_ifrs_facts": 2,
            "needs_manual_review": 1,
            "status_counts": {"confirmed_by_smartlab": 1, "needs_review": 1},
            "source_counts": {"official_ifrs_xlsx": 2},
            "companies": ["TEST"],
        }

    monkeypatch.setattr("src.pipeline.run_all.write_extracted_ifrs_audit", fake_extracted_ifrs_audit)

    summary = run_all(tmp_path, smartlab_only=False, skip_ocr=True, no_network=True)

    assert summary["extracted_ifrs_facts"] == 2
    assert summary["extracted_ifrs_needs_review"] == 1
    assert summary["extracted_ifrs_status_counts"] == {"confirmed_by_smartlab": 1, "needs_review": 1}


def test_run_all_summary_includes_smartlab_panel_outlier_counts(tmp_path: Path):
    panel_dir = tmp_path / "data" / "panels_final"
    panel_dir.mkdir(parents=True)
    pd.DataFrame([
        {
            "ticker": "BAD",
            "year": 2024,
            "sector": "Retail",
            "revenue_mln": -10,
            "assets_mln": 100,
            "cash_mln": 10,
        }
    ]).to_csv(panel_dir / "panel_russia_final_smartlab.csv", index=False)

    summary = run_all(tmp_path, smartlab_only=False, skip_ocr=True, no_network=True)

    assert summary["smartlab_panel_outliers_count"] == 1
    assert summary["smartlab_panel_outliers_by_issue"] == {"negative_value": 1}
    assert (tmp_path / "data" / "manual_review" / "smartlab_panel_outliers.csv").exists()
