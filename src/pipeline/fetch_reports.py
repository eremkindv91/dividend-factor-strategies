from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.io_utils import stable_id, utc_now_iso, write_parquet
from src.paths import REPO_ROOT


REPORT_COLUMNS = [
    "report_id", "ticker", "inn", "company_name", "period", "period_type", "fiscal_year",
    "publication_date", "document_title", "document_type", "reporting_standard", "file_path",
    "source_url", "source_name", "file_hash", "download_status", "parse_status",
    "extraction_status", "quality_status", "created_at", "updated_at",
]


def fetch_reports(
    root: Path = REPO_ROOT,
    from_year: int | None = None,
    to_year: int | None = None,
    ticker: str | None = None,
    no_network: bool = True,
    *_args,
    **_kwargs,
) -> dict:
    source_path = root / "data" / "company_sources.csv"
    path = root / "data" / "report_index.parquet"
    now = utc_now_iso()
    rows = []
    if source_path.exists():
        src = pd.read_csv(source_path, dtype=str).fillna("")
        src = src[src.get("active", "true").astype(str).str.lower().isin(["", "true", "1", "yes"])]
        if ticker:
            src = src[src["ticker"].astype(str).str.upper() == str(ticker).upper()]
        report_like = src["source_type"].isin(["manual_report", "report_pdf", "report_xlsx", "ifrs_report"])
        src = src[report_like]
        for _, r in src.iterrows():
            fy = _int_or_none(r.get("fiscal_year"))
            if from_year is not None and fy is not None and fy < from_year:
                continue
            if to_year is not None and fy is not None and fy > to_year:
                continue
            source_url = str(r.get("source_url", "")).strip()
            if not source_url:
                continue
            title = r.get("document_title") or f"{r.get('ticker')} report {r.get('period') or r.get('fiscal_year')}"
            report_id = stable_id("report", r.get("ticker"), r.get("period"), r.get("reporting_standard"), source_url)
            rows.append({
                "report_id": report_id,
                "ticker": str(r.get("ticker", "")).strip().upper(),
                "inn": r.get("inn", ""),
                "company_name": r.get("company_name", ""),
                "period": r.get("period") or (str(fy) if fy is not None else ""),
                "period_type": r.get("period_type") or "annual",
                "fiscal_year": fy,
                "publication_date": "",
                "document_title": title,
                "document_type": r.get("document_type") or "unknown",
                "reporting_standard": r.get("reporting_standard") or "UNKNOWN",
                "file_path": "",
                "source_url": source_url,
                "source_name": r.get("source_type") or "manual_report",
                "file_hash": "",
                "download_status": "metadata_only" if no_network else "pending_download",
                "parse_status": "not_parsed",
                "extraction_status": "not_extracted",
                "quality_status": "not_checked",
                "created_at": now,
                "updated_at": now,
            })

    out = pd.DataFrame(rows, columns=REPORT_COLUMNS).drop_duplicates("report_id") if rows else pd.DataFrame(columns=REPORT_COLUMNS)
    write_parquet(path, out)
    return {
        "reports_found": int(len(out)),
        "reports_downloaded": 0,
        "mode": "metadata_only_no_network" if no_network else "metadata_pending_download",
    }


def _int_or_none(value) -> int | None:
    try:
        if value is None or pd.isna(value) or str(value).strip() == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--from-year", type=int)
    parser.add_argument("--to-year", type=int)
    parser.add_argument("--ticker")
    parser.add_argument("--no-network", action="store_true")
    parser.add_argument("--allow-network", action="store_true", help="Reserved for a later downloader stage; current implementation remains metadata-only.")
    args = parser.parse_args()
    res = fetch_reports(Path(args.repo_root), from_year=args.from_year, to_year=args.to_year, ticker=args.ticker, no_network=args.no_network or not args.allow_network)
    print(f"[fetch-reports] reports_downloaded={res['reports_downloaded']} mode={res['mode']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
