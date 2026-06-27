from pathlib import Path

import pandas as pd

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
