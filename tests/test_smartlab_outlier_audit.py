from pathlib import Path
import json

import pandas as pd

from src.quality.audit_smartlab_outliers import (
    build_smartlab_outlier_audit,
    build_smartlab_outlier_triage,
    build_smartlab_unit_mismatch_override_candidates,
    build_smartlab_unit_mismatch_review,
    write_smartlab_outlier_audit,
    write_smartlab_outlier_triage,
)


def test_smartlab_outlier_audit_flags_panel_anomalies_without_modifying_panel(tmp_path: Path):
    panel_dir = tmp_path / "data" / "panels_final"
    panel_dir.mkdir(parents=True)
    panel_path = panel_dir / "panel_russia_final_smartlab.csv"
    original = pd.DataFrame([
        {
            "ticker": "BAD",
            "year": 2023,
            "sector": "Retail",
            "revenue_mln": 100.0,
            "assets_mln": 200.0,
            "cash_mln": 50.0,
            "total_debt_mln": 80.0,
        },
        {
            "ticker": "BAD",
            "year": 2024,
            "sector": "Retail",
            "revenue_mln": 2_500.0,
            "assets_mln": 200.0,
            "cash_mln": 500.0,
            "total_debt_mln": 80.0,
        },
        {
            "ticker": "NEG",
            "year": 2024,
            "sector": "Tech",
            "revenue_mln": -10.0,
            "assets_mln": 100.0,
            "cash_mln": 10.0,
            "total_debt_mln": 20.0,
        },
    ])
    original.to_csv(panel_path, index=False)

    audit = build_smartlab_outlier_audit(tmp_path)

    issues = set(audit["issue"])
    assert "negative_value" in issues
    assert "cash_exceeds_assets" in issues
    assert "yoy_jump_gt_10x" in issues

    summary = write_smartlab_outlier_audit(tmp_path)
    after = pd.read_csv(panel_path)
    rows = pd.read_csv(tmp_path / "data" / "manual_review" / "smartlab_panel_outliers.csv")
    saved_summary = json.loads((tmp_path / "data" / "manual_review" / "smartlab_panel_outliers_summary.json").read_text())

    pd.testing.assert_frame_equal(after, original)
    assert summary["total_outliers"] == len(rows)
    assert saved_summary["meta"]["total_outliers"] == len(rows)
    assert summary["companies"] == ["BAD", "NEG"]


def test_smartlab_outlier_triage_marks_likely_unit_mismatch_without_correction(tmp_path: Path):
    panel_dir = tmp_path / "data" / "panels_final"
    panel_dir.mkdir(parents=True)
    panel_path = panel_dir / "panel_russia_final_smartlab.csv"
    original = pd.DataFrame([
        {
            "ticker": "UNIT",
            "year": 2022,
            "sector": "Retail",
            "revenue_mln": 1_000_000.0,
            "assets_mln": 2_000_000.0,
        },
        {
            "ticker": "UNIT",
            "year": 2023,
            "sector": "Retail",
            "revenue_mln": 1_200.0,
            "assets_mln": 2_100.0,
        },
    ])
    original.to_csv(panel_path, index=False)

    triage = build_smartlab_outlier_triage(tmp_path)

    revenue = triage[triage["field"].eq("revenue_mln")].iloc[0]
    assert revenue["triage_status"] == "suspected_unit_mismatch"
    assert revenue["suspect_year"] == 2022
    assert revenue["suspect_value"] == 1_000_000.0
    assert revenue["suggested_scale_divisor"] == 1000
    assert revenue["candidate_corrected_value"] == 1_000.0
    assert revenue["suggested_action"] == "manual_source_check_before_panel_edit"
    assert revenue["batch_key"] == "unit_mismatch:UNIT:2022"

    summary = write_smartlab_outlier_triage(tmp_path)
    after = pd.read_csv(panel_path)
    saved = pd.read_csv(tmp_path / "data" / "manual_review" / "smartlab_panel_outlier_triage.csv")
    batches = pd.read_csv(tmp_path / "data" / "manual_review" / "smartlab_panel_outlier_batches.csv")

    pd.testing.assert_frame_equal(after, original)
    assert summary["suspected_unit_mismatch"] == len(saved)
    assert summary["batch_count"] == 1
    assert len(batches) == 1
    assert batches.loc[0, "batch_key"] == "unit_mismatch:UNIT:2022"
    assert batches.loc[0, "rows"] == 2
    assert batches.loc[0, "fields"] == "assets_mln,revenue_mln"
    assert batches.loc[0, "suggested_action"] == "manual_source_check_before_panel_edit"


def test_smartlab_unit_mismatch_review_classifies_batches_without_panel_edits(tmp_path: Path):
    panel_dir = tmp_path / "data" / "panels_final"
    panel_dir.mkdir(parents=True)
    panel_path = panel_dir / "panel_russia_final_smartlab.csv"
    original = pd.DataFrame([
        {
            "ticker": "FULL",
            "year": 2022,
            "sector": "Steel",
            "revenue_mln": 1_000_000.0,
            "ebitda_mln": 180_000.0,
            "net_profit_mln": 90_000.0,
            "equity_mln": 1_400_000.0,
            "assets_mln": 2_000_000.0,
            "net_debt_mln": 100_000.0,
            "cash_mln": 250_000.0,
            "total_debt_mln": 350_000.0,
            "market_cap_mln": 900_000.0,
            "CFO_": 120_000.0,
            "CAPEX_": -50_000.0,
            "FCF": 70_000.0,
        },
        {
            "ticker": "FULL",
            "year": 2023,
            "sector": "Steel",
            "revenue_mln": 1_100.0,
            "ebitda_mln": 190.0,
            "net_profit_mln": 95.0,
            "equity_mln": 1_450.0,
            "assets_mln": 2_050.0,
            "net_debt_mln": 105.0,
            "cash_mln": 260.0,
            "total_debt_mln": 360.0,
            "market_cap_mln": 950.0,
            "CFO_": 130.0,
            "CAPEX_": -55.0,
            "FCF": 75.0,
        },
        {
            "ticker": "ONE",
            "year": 2022,
            "sector": "Utilities",
            "revenue_mln": 100.0,
            "FCF": 50.0,
        },
        {
            "ticker": "ONE",
            "year": 2023,
            "sector": "Utilities",
            "revenue_mln": 105.0,
            "FCF": 120_000.0,
        },
    ])
    original.to_csv(panel_path, index=False)

    triage = build_smartlab_outlier_triage(tmp_path)
    review = build_smartlab_unit_mismatch_review(triage)

    by_ticker = review.set_index("ticker")
    assert by_ticker.loc["FULL", "review_status"] == "probable_full_company_unit_mismatch"
    assert by_ticker.loc["FULL", "suggested_action"] == "verify_source_then_create_manual_override"
    assert bool(by_ticker.loc["FULL", "do_not_auto_correct"]) is True
    assert by_ticker.loc["ONE", "review_status"] == "single_field_needs_source_check"
    assert by_ticker.loc["ONE", "suggested_action"] == "manual_source_check_only"
    assert bool(by_ticker.loc["ONE", "do_not_auto_correct"]) is True

    summary = write_smartlab_outlier_triage(tmp_path)
    after = pd.read_csv(panel_path)
    saved = pd.read_csv(tmp_path / "data" / "manual_review" / "smartlab_unit_mismatch_review.csv")
    saved_summary = json.loads((tmp_path / "data" / "manual_review" / "smartlab_unit_mismatch_review_summary.json").read_text())

    pd.testing.assert_frame_equal(after, original)
    assert summary["review_batches"] == 2
    assert summary["review_probable_full_company_unit_mismatch"] == 1
    assert summary["review_single_field_needs_source_check"] == 1
    assert saved_summary["meta"]["do_not_auto_correct"] is True
    assert len(saved) == 2


def test_smartlab_unit_mismatch_override_candidates_are_review_templates_only(tmp_path: Path):
    panel_dir = tmp_path / "data" / "panels_final"
    panel_dir.mkdir(parents=True)
    panel_path = panel_dir / "panel_russia_final_smartlab.csv"
    original = pd.DataFrame([
        {
            "ticker": "FULL",
            "year": 2022,
            "sector": "Steel",
            "revenue_mln": 1_000_000.0,
            "ebitda_mln": 180_000.0,
            "net_profit_mln": 90_000.0,
            "equity_mln": 1_400_000.0,
            "assets_mln": 2_000_000.0,
            "net_debt_mln": 100_000.0,
            "cash_mln": 250_000.0,
            "total_debt_mln": 350_000.0,
            "market_cap_mln": 900_000.0,
            "CFO_": 120_000.0,
            "CAPEX_": -50_000.0,
            "FCF": 70_000.0,
        },
        {
            "ticker": "FULL",
            "year": 2023,
            "sector": "Steel",
            "revenue_mln": 1_100.0,
            "ebitda_mln": 190.0,
            "net_profit_mln": 95.0,
            "equity_mln": 1_450.0,
            "assets_mln": 2_050.0,
            "net_debt_mln": 105.0,
            "cash_mln": 260.0,
            "total_debt_mln": 360.0,
            "market_cap_mln": 950.0,
            "CFO_": 130.0,
            "CAPEX_": -55.0,
            "FCF": 75.0,
        },
    ])
    original.to_csv(panel_path, index=False)

    triage = build_smartlab_outlier_triage(tmp_path)
    review = build_smartlab_unit_mismatch_review(triage)
    candidates = build_smartlab_unit_mismatch_override_candidates(triage, review)

    assert len(candidates) == len(triage[triage["triage_status"].eq("suspected_unit_mismatch")])
    revenue = candidates[candidates["line_item_std"].eq("revenue")].iloc[0]
    assert revenue["ticker"] == "FULL"
    assert revenue["fiscal_year"] == 2022
    assert revenue["period"] == "2022"
    assert revenue["reporting_standard"] == "IFRS"
    assert revenue["statement_type"] == "income_statement"
    assert revenue["value_normalized"] == 1_000_000_000.0
    assert revenue["source_url"] == ""
    assert revenue["source_verification_status"] == "needs_source_verification"
    assert bool(revenue["ready_for_manual_override"]) is False

    summary = write_smartlab_outlier_triage(tmp_path)
    after = pd.read_csv(panel_path)
    saved = pd.read_csv(tmp_path / "data" / "manual_review" / "smartlab_unit_mismatch_override_candidates.csv")

    pd.testing.assert_frame_equal(after, original)
    assert summary["override_candidate_rows"] == len(candidates)
    assert summary["override_candidate_ready"] == 0
    assert len(saved) == len(candidates)
