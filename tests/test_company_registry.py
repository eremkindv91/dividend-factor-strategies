from pathlib import Path

import pandas as pd

from src.pipeline.migrate_existing_smartlab_data import migrate_smartlab


def test_registry_is_idempotent_and_preserves_manual_fields(tmp_path: Path):
    panel_dir = tmp_path / "data" / "panels_final"
    panel_dir.mkdir(parents=True)
    pd.DataFrame([
        {"ticker": "TEST", "year": 2025, "sector": "IT", "revenue_mln": 10},
    ]).to_csv(panel_dir / "panel_russia_final_smartlab.csv", index=False)

    reg_path = tmp_path / "data" / "companies_registry.csv"
    reg_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([
        {
            "ticker": "TEST",
            "secid": "TEST",
            "short_name": "Manual Name",
            "full_name": "",
            "inn": "123",
            "ogrn": "",
            "sector": "",
            "industry": "",
            "moex_board": "",
            "is_public": "",
            "is_traded": "",
            "has_smartlab_data": "",
            "has_ifrs": "",
            "has_ras": "",
            "disclosure_page_url": "",
            "e_disclosure_company_id": "",
            "issuer_website": "",
            "source_priority": "",
            "status": "",
            "coverage_status": "",
            "last_report_period": "",
            "last_successful_update": "",
            "notes": "",
        }
    ]).to_csv(reg_path, index=False)

    migrate_smartlab(tmp_path, make_backup=False)
    migrate_smartlab(tmp_path, make_backup=False)
    reg = pd.read_csv(reg_path, dtype=str).fillna("")

    assert len(reg) == 1
    assert reg.loc[0, "short_name"] == "Manual Name"
    assert reg.loc[0, "inn"] == "123"
    assert reg.loc[0, "coverage_status"] == "smartlab_only"
