from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

from src.io_utils import stable_id, utc_now_iso, write_csv, write_parquet
from src.paths import REPO_ROOT


REPORT_COLUMNS = [
    "report_id", "ticker", "inn", "company_name", "period", "period_type", "fiscal_year",
    "publication_date", "document_title", "document_type", "reporting_standard", "file_path",
    "source_url", "source_name", "file_hash", "download_status", "parse_status",
    "extraction_status", "quality_status", "url_status", "http_status", "content_type",
    "content_length", "final_url", "url_checked_at", "created_at", "updated_at",
]

SOURCE_ERROR_COLUMNS = [
    "ticker", "source_type", "source_url", "issue", "detail", "created_at",
]


def fetch_reports(
    root: Path = REPO_ROOT,
    from_year: int | None = None,
    to_year: int | None = None,
    ticker: str | None = None,
    limit_companies: int | None = None,
    no_network: bool = True,
    metadata_checker=None,
    *_args,
    **_kwargs,
) -> dict:
    source_path = root / "data" / "company_sources.csv"
    path = root / "data" / "report_index.parquet"
    now = utc_now_iso()
    rows = []
    errors = []
    if not source_path.exists() and path.exists():
        existing = pd.read_parquet(path)
        return {
            "reports_found": int(len(existing)),
            "reports_downloaded": 0,
            "source_errors": 0,
            "mode": "existing_report_index",
        }
    if source_path.exists():
        src = pd.read_csv(source_path, dtype=str).fillna("")
        src = src[src.get("active", "true").astype(str).str.lower().isin(["", "true", "1", "yes"])]
        if ticker:
            src = src[src["ticker"].astype(str).str.upper() == str(ticker).upper()]
        elif limit_companies is not None and limit_companies > 0 and "ticker" in src:
            tickers = []
            seen = set()
            for raw in src["ticker"].astype(str):
                value = raw.strip().upper()
                if value and value not in seen:
                    seen.add(value)
                    tickers.append(value)
                if len(tickers) >= limit_companies:
                    break
            src = src[src["ticker"].astype(str).str.upper().isin(set(tickers))]
        report_like = src["source_type"].isin(["manual_report", "report_pdf", "report_xlsx", "ifrs_report"])
        src = src[report_like]
        if src.empty and path.exists():
            existing = pd.read_parquet(path)
            return {
                "reports_found": int(len(existing)),
                "reports_downloaded": 0,
                "source_errors": 0,
                "mode": "existing_report_index",
            }
        for _, r in src.iterrows():
            fy = _int_or_none(r.get("fiscal_year"))
            if from_year is not None and fy is not None and fy < from_year:
                continue
            if to_year is not None and fy is not None and fy > to_year:
                continue
            source_url = str(r.get("source_url", "")).strip()
            if not source_url:
                continue
            issue = validate_source_url(source_url)
            if issue:
                errors.append({
                    "ticker": str(r.get("ticker", "")).strip().upper(),
                    "source_type": r.get("source_type", ""),
                    "source_url": source_url,
                    "issue": issue,
                    "detail": "",
                    "created_at": now,
                })
                continue
            meta = {
                "ok": None,
                "http_status": None,
                "content_type": "",
                "content_length": None,
                "final_url": source_url,
                "error": "",
            }
            if not no_network:
                checker = metadata_checker or check_url_metadata
                meta = checker(source_url)
                if not meta.get("ok"):
                    errors.append({
                        "ticker": str(r.get("ticker", "")).strip().upper(),
                        "source_type": r.get("source_type", ""),
                        "source_url": source_url,
                        "issue": "url_metadata_error",
                        "detail": str(meta.get("error") or meta.get("http_status") or ""),
                        "created_at": now,
                    })
            title = r.get("document_title") or f"{r.get('ticker')} report {r.get('period') or r.get('fiscal_year')}"
            report_id = stable_id("report", r.get("ticker"), r.get("period"), r.get("reporting_standard"), source_url)
            url_ok = bool(meta.get("ok")) if not no_network else None
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
                "download_status": "metadata_only" if no_network else ("metadata_checked" if url_ok else "url_error"),
                "parse_status": "not_parsed",
                "extraction_status": "not_extracted",
                "quality_status": "not_checked" if no_network else ("source_url_ok" if url_ok else "source_url_error"),
                "url_status": "not_checked" if no_network else ("ok" if url_ok else "error"),
                "http_status": meta.get("http_status"),
                "content_type": meta.get("content_type") or "",
                "content_length": meta.get("content_length"),
                "final_url": meta.get("final_url") or source_url,
                "url_checked_at": "" if no_network else now,
                "created_at": now,
                "updated_at": now,
            })

    out = pd.DataFrame(rows, columns=REPORT_COLUMNS).drop_duplicates("report_id") if rows else pd.DataFrame(columns=REPORT_COLUMNS)
    write_parquet(path, out)
    write_csv(root / "data" / "manual_review" / "report_source_errors.csv", errors, SOURCE_ERROR_COLUMNS)
    return {
        "reports_found": int(len(out)),
        "reports_downloaded": 0,
        "source_errors": len(errors),
        "mode": "metadata_only_no_network" if no_network else "metadata_pending_download",
    }


def validate_source_url(url: str) -> str | None:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        return "invalid_url_scheme"
    if not parsed.netloc:
        return "missing_url_host"
    return None


def check_url_metadata(url: str, timeout: int = 10) -> dict:
    try:
        import requests

        headers = {"User-Agent": "dividend-factor-strategies/metadata-check"}
        resp = requests.head(url, allow_redirects=True, timeout=timeout, headers=headers)
        if resp.status_code in {405, 403}:
            resp = requests.get(url, allow_redirects=True, timeout=timeout, headers={**headers, "Range": "bytes=0-0"}, stream=True)
            resp.close()
        content_length = resp.headers.get("content-length")
        return {
            "url": url,
            "ok": 200 <= resp.status_code < 400,
            "http_status": int(resp.status_code),
            "content_type": resp.headers.get("content-type", ""),
            "content_length": _int_or_none(content_length),
            "final_url": resp.url,
            "error": "",
        }
    except Exception as e:  # noqa: BLE001
        return {
            "url": url,
            "ok": False,
            "http_status": None,
            "content_type": "",
            "content_length": None,
            "final_url": url,
            "error": str(e),
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
    parser.add_argument("--limit-companies", type=int)
    parser.add_argument("--no-network", action="store_true")
    parser.add_argument("--allow-network", action="store_true", help="Reserved for a later downloader stage; current implementation remains metadata-only.")
    args = parser.parse_args()
    res = fetch_reports(
        Path(args.repo_root),
        from_year=args.from_year,
        to_year=args.to_year,
        ticker=args.ticker,
        limit_companies=args.limit_companies,
        no_network=args.no_network or not args.allow_network,
    )
    print(f"[fetch-reports] reports_downloaded={res['reports_downloaded']} mode={res['mode']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
