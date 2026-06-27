from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.data_sources.moex_adapter import CompanySecurity, fetch_moex_shares
from src.paths import REPO_ROOT
from src.pipeline.migrate_existing_smartlab_data import REGISTRY_FIELDS, update_registry_from_panel
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


def discover_companies(root: Path = REPO_ROOT, moex_securities: list[CompanySecurity] | None = None, use_moex: bool = True) -> dict:
    reg_path = root / "data" / "companies_registry.csv"
    mode = "registry_only"
    now = utc_now_iso()
    if not reg_path.exists():
        panel = root / "data" / "panels_final" / "panel_russia_final_smartlab.csv"
        if not panel.exists():
            panel = root / "data" / "panels_final" / "panel_russia_final.csv"
        if not panel.exists():
            raise FileNotFoundError("No companies_registry.csv or local panel source found.")
        df = pd.read_csv(panel)
        registry = update_registry_from_panel(root, df, now)
        mode = "local_panel_registry"
    else:
        registry = {"companies": len(pd.read_csv(reg_path, dtype=str).fillna(""))}

    moex_summary = {"checked": 0, "new_companies_added": 0, "errors": 0}
    if use_moex:
        try:
            securities = moex_securities if moex_securities is not None else fetch_moex_shares()
            moex_summary = merge_moex_companies(root, securities, now)
            registry = {"companies": registry_count(root)}
            mode = f"{mode}+moex"
        except Exception:  # noqa: BLE001
            moex_summary = {"checked": 0, "new_companies_added": 0, "errors": 1}

    source_rows = build_company_sources(root)
    summary = {
        "new_companies_added": moex_summary["new_companies_added"],
        "registry_companies": registry["companies"],
        "source_rows": len(source_rows),
        "mode": mode,
        "moex_checked": moex_summary["checked"],
        "moex_errors": moex_summary["errors"],
    }
    write_json(root / "data" / "pipeline_summary.json", {"discover_companies": summary})
    return summary


def merge_moex_companies(root: Path, securities: list[CompanySecurity], now: str) -> dict:
    reg_path = root / "data" / "companies_registry.csv"
    existing: dict[str, dict] = {}
    if reg_path.exists():
        old = pd.read_csv(reg_path, dtype=str).fillna("")
        for _, row in old.iterrows():
            current = {k: row.get(k, "") for k in REGISTRY_FIELDS}
            existing[str(current.get("ticker", "")).strip().upper()] = current

    active_tickers = {str(sec.secid or "").strip().upper() for sec in securities if str(sec.secid or "").strip()}
    existing = {
        ticker: row
        for ticker, row in existing.items()
        if not (
            str(row.get("notes", "")).strip() == "created_from_moex_discovery"
            and ticker not in active_tickers
        )
    }

    added = 0
    for sec in securities:
        ticker = str(sec.secid or "").strip().upper()
        if not ticker:
            continue
        current = existing.get(ticker)
        if current is None:
            current = {k: "" for k in REGISTRY_FIELDS}
            current.update({
                "ticker": ticker,
                "secid": ticker,
                "short_name": sec.short_name or ticker,
                "full_name": sec.sec_name,
                "moex_board": sec.board,
                "is_public": "true",
                "is_traded": "true",
                "has_smartlab_data": "false",
                "has_ifrs": "",
                "has_ras": "",
                "source_priority": "8",
                "status": "needs_mapping",
                "coverage_status": "candidate",
                "last_successful_update": now,
                "notes": "created_from_moex_discovery",
            })
            added += 1
        else:
            if not str(current.get("secid", "")).strip():
                current["secid"] = ticker
            if not str(current.get("moex_board", "")).strip():
                current["moex_board"] = sec.board
            current["is_traded"] = "true"
            if not str(current.get("full_name", "")).strip() and sec.sec_name:
                current["full_name"] = sec.sec_name
        existing[ticker] = current

    write_csv(reg_path, [existing[k] for k in sorted(existing)], REGISTRY_FIELDS)
    return {"checked": len(securities), "new_companies_added": added, "errors": 0}


def registry_count(root: Path) -> int:
    path = root / "data" / "companies_registry.csv"
    if not path.exists():
        return 0
    return len(pd.read_csv(path, dtype=str).fillna(""))


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
    parser.add_argument("--no-moex", action="store_true")
    args = parser.parse_args()
    res = discover_companies(Path(args.repo_root), use_moex=not args.no_moex)
    print(f"[discover] registry_companies={res['registry_companies']} mode={res['mode']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
