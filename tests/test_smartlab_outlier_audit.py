from pathlib import Path
import json

import pandas as pd

from src.quality.audit_smartlab_outliers import build_smartlab_outlier_audit, write_smartlab_outlier_audit


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
