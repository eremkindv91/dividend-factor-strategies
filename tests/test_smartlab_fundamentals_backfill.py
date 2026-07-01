from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.pipeline.build_site_data import build_site_data
from src.quality.audit_fundamental_values import (
    build_smartlab_fundamentals_cleaned,
    write_smartlab_fundamentals_cleaned,
)


def _write_minimal_unified(root: Path) -> None:
    unified = root / "data" / "unified"
    unified.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([
        {
            "ticker": "TEST",
            "fiscal_year": 2024,
            "period": "2024",
            "revenue": 100.0,
            "quality_score": 70.0,
            "source": "smartlab",
            "conflict_flag": False,
            "needs_manual_review": False,
            "ocr_candidate": False,
        }
    ]).to_parquet(unified / "company_financials_unified.parquet", index=False)
    pd.DataFrame([
        {
            "ticker": "TEST",
            "fiscal_year": 2024,
            "period": "2024",
            "line_item_std": "revenue",
            "best_source_name": "smartlab",
            "best_quality_score": 70.0,
            "conflict_flag": False,
            "needs_manual_review": False,
            "is_reliable": True,
        }
    ]).to_parquet(unified / "financial_facts_unified.parquet", index=False)


def test_backfill_maps_raw_alias_and_calculates_missing_cashflow_without_overwriting_panel(tmp_path: Path):
    panel_dir = tmp_path / "data" / "panels_final"
    panel_dir.mkdir(parents=True)
    panel_path = panel_dir / "panel_russia_final_smartlab.csv"
    original = pd.DataFrame([
        {
            "ticker": "TEST",
            "year": 2024,
            "revenue_mln": 100.0,
            "net_profit_mln": 20.0,
            "assets_mln": 300.0,
            "equity_mln": 120.0,
            "total_debt_mln": 80.0,
            "cash_mln": 30.0,
            "operating_cash_flow": 55.0,
            "CAPEX_": -15.0,
            "market_cap_mln": 1_000.0,
            "price_end": 10.0,
        }
    ])
    original.to_csv(panel_path, index=False)

    summary = write_smartlab_fundamentals_cleaned(tmp_path)
    cleaned = pd.read_parquet(tmp_path / "data" / "processed" / "smartlab_fundamentals_cleaned.parquet")
    backfilled = pd.read_parquet(tmp_path / "data" / "processed" / "smartlab_fundamentals_backfilled.parquet")
    gap_summary = json.loads((tmp_path / "data" / "quality" / "smartlab_fundamentals_gap_summary.json").read_text())
    after = pd.read_csv(panel_path)

    pd.testing.assert_frame_equal(after, original)
    rows = {(r.field, r.year): r for r in cleaned.itertuples()}
    assert rows[("operating_cash_flow", 2024)].raw_field == "operating_cash_flow"
    assert rows[("operating_cash_flow", 2024)].source_status == "mapped_from_raw_existing"
    assert rows[("free_cash_flow", 2024)].clean_value == 40.0
    assert rows[("free_cash_flow", 2024)].source_status == "calculated_from_clean_base_facts"
    assert rows[("net_debt", 2024)].clean_value == 50.0
    assert rows[("net_debt", 2024)].source_status == "calculated_from_clean_base_facts"

    statuses = set(backfilled["backfill_status"])
    assert "mapped_from_raw_existing" in statuses
    assert "calculated_from_base_facts" in statuses
    assert summary["smartlab_backfilled_values"] >= 1
    assert summary["smartlab_calculated_values"] >= 2
    assert gap_summary["missing_can_be_calculated"] >= 1


def test_backfill_does_not_calculate_ratios_with_unreliable_denominator(tmp_path: Path):
    panel_dir = tmp_path / "data" / "panels_final"
    panel_dir.mkdir(parents=True)
    pd.DataFrame([
        {
            "ticker": "BAD",
            "year": 2024,
            "revenue_mln": 0.0,
            "net_profit_mln": 10.0,
            "assets_mln": 100.0,
            "equity_mln": 0.0,
        }
    ]).to_csv(panel_dir / "panel_russia_final_smartlab.csv", index=False)

    write_smartlab_fundamentals_cleaned(tmp_path)
    cleaned = build_smartlab_fundamentals_cleaned(tmp_path, now="2026-06-30T00:00:00+00:00")
    backfilled = pd.read_parquet(tmp_path / "data" / "processed" / "smartlab_fundamentals_backfilled.parquet")

    fields = set(cleaned["field"])
    assert "net_margin" not in fields
    assert "roe" not in fields
    blocked = backfilled[
        (backfilled["ticker"] == "BAD")
        & (backfilled["normalized_field"].isin(["net_margin", "roe"]))
    ]
    assert set(blocked["backfill_status"]) == {"failed"}
    assert set(blocked["backfill_reason"]) == {"unreliable_denominator"}


def test_backfilled_value_reaches_site_fundamentals(tmp_path: Path):
    panel_dir = tmp_path / "data" / "panels_final"
    panel_dir.mkdir(parents=True)
    pd.DataFrame([
        {
            "ticker": "TEST",
            "year": 2024,
            "revenue_mln": 100.0,
            "net_profit_mln": 20.0,
            "CFO_": 60.0,
            "CAPEX_": -25.0,
        }
    ]).to_csv(panel_dir / "panel_russia_final_smartlab.csv", index=False)
    _write_minimal_unified(tmp_path)

    build_site_data(tmp_path)
    financials = json.loads((tmp_path / "data" / "unified" / "site_financials.json").read_text())
    cashflow = financials["fundamentals"]["TEST"]["cashflow"]
    fcf = next(metric for metric in cashflow if metric["field"] == "free_cash_flow")

    assert fcf["source_status"] == "calculated_from_clean_base_facts"
    assert fcf["values"][0]["value"] == 35.0
    assert (tmp_path / "data" / "quality" / "smartlab_fundamentals_gap_report.csv").exists()
    assert (tmp_path / "data" / "quality" / "sample_company_card_coverage.csv").exists()
