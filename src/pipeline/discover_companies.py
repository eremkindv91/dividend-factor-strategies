from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.paths import REPO_ROOT
from src.pipeline.migrate_existing_smartlab_data import update_registry_from_panel
from src.io_utils import utc_now_iso, write_csv, write_json


SOURCE_COLUMNS = [
    "ticker",
    "inn",
    "company_name",
    "source_type",
    "source_url",
    "document_title",
    "document_type",
    "reporting_standard",
    "fiscal_year",
    "period",
    "period_type",
    "priority",
    "active",
    "notes",
]


def discover_companies(root: Path = REPO_ROOT) -> dict:
    reg_path = root / "data" / "companies_registry.csv"
    mode = "registry_only"
    if not reg_path.exists():
        panel = root / "data" / "panels_final" / "panel_russia_final_smartlab.csv"
        if not panel.exists():
            panel = root / "data" / "panels_final" / "panel_russia_final.csv"
        if not panel.exists():
            raise FileNotFoundError("No companies_registry.csv or local panel source found.")
        df = pd.read_csv(panel)
        registry = update_registry_from_panel(root, df, utc_now_iso())
        mode = "local_panel_registry"
    else:
        registry = {"companies": len(pd.read_csv(reg_path, dtype=str).fillna(""))}

    source_rows = build_company_sources(root)
    summary = {
        "new_companies_added": None,
        "registry_companies": registry["companies"],
        "source_rows": len(source_rows),
        "mode": mode,
    }
    write_json(root / "data" / "pipeline_summary.json", {"discover_companies": summary})
    return summary


def build_company_sources(root: Path = REPO_ROOT) -> list[dict]:
    reg_path = root / "data" / "companies_registry.csv"
    src_path = root / "data" / "company_sources.csv"
    rows: list[dict] = []

    if src_path.exists():
        existing = pd.read_csv(src_path, dtype=str).fillna("")
        rows.extend(existing.to_dict(orient="records"))

    if reg_path.exists():
        reg = pd.read_csv(reg_path, dtype=str).fillna("")
        for _, r in reg.iterrows():
            ticker = str(r.get("ticker", "")).strip().upper()
            if not ticker:
                continue
            common = {
                "ticker": ticker,
                "inn": r.get("inn", ""),
                "company_name": r.get("full_name") or r.get("short_name") or ticker,
                "document_title": "",
                "document_type": "",
                "reporting_standard": "",
                "fiscal_year": "",
                "period": "",
                "period_type": "",
                "priority": 5,
                "active": "true",
            }
            disclosure = str(r.get("disclosure_page_url", "")).strip()
            if disclosure:
                rows.append({**common, "source_type": "disclosure_page", "source_url": disclosure, "notes": "from_companies_registry"})
            issuer = str(r.get("issuer_website", "")).strip()
            if issuer:
                rows.append({**common, "source_type": "issuer_website", "source_url": issuer, "notes": "from_companies_registry"})

    dedup: dict[tuple[str, str, str], dict] = {}
    for row in rows:
        normalized = {k: row.get(k, "") for k in SOURCE_COLUMNS}
        normalized["ticker"] = str(normalized["ticker"]).strip().upper()
        key = (normalized["ticker"], str(normalized["source_type"]).strip(), str(normalized["source_url"]).strip())
        if key[0] and key[1] and key[2]:
            dedup[key] = normalized
    out = [dedup[k] for k in sorted(dedup)]
    write_csv(src_path, out, SOURCE_COLUMNS)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    args = parser.parse_args()
    res = discover_companies(Path(args.repo_root))
    print(f"[discover] registry_companies={res['registry_companies']} mode={res['mode']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
