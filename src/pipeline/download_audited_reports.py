from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

from src.io_utils import utc_now_iso, write_parquet
from src.paths import REPO_ROOT, ensure_dir
from src.pipeline.fetch_reports import download_url


def download_audited_reports(
    root: Path = REPO_ROOT,
    audit_status: str = "ok",
    downloader=None,
    now: str | None = None,
) -> dict:
    now = now or utc_now_iso()
    report_index_path = root / "data" / "report_index.parquet"
    audit_path = root / "results" / "disclosure" / "report_index_audit.csv"
    if not report_index_path.exists():
        raise FileNotFoundError("data/report_index.parquet is missing. Run fetch_reports first.")
    if not audit_path.exists():
        raise FileNotFoundError("results/disclosure/report_index_audit.csv is missing. Run audit_disclosure_links first.")

    reports = pd.read_parquet(report_index_path)
    audit = pd.read_csv(audit_path, dtype=str).fillna("")
    eligible_urls = set(
        audit.loc[
            audit["audit_status"].astype(str).str.lower() == audit_status.lower(),
            "source_url",
        ].astype(str).str.strip()
    )
    fetcher = downloader or download_url
    out = reports.copy()
    downloaded = 0
    failed = 0
    skipped_not_ok = 0
    skipped_non_file_ok = 0
    errors: list[dict] = []

    for idx, row in out.iterrows():
        url = str(row.get("source_url") or "").strip()
        if url not in eligible_urls:
            skipped_not_ok += 1
            continue
        if not is_downloadable_report_file(row):
            skipped_non_file_ok += 1
            continue
        try:
            result = fetcher(url)
        except Exception as e:  # noqa: BLE001
            result = {"ok": False, "error": str(e), "content": b"", "http_status": None}
        if not result.get("ok"):
            failed += 1
            errors.append({
                "ticker": row.get("ticker"),
                "source_url": url,
                "issue": "download_error",
                "detail": str(result.get("error") or result.get("http_status") or ""),
                "created_at": now,
            })
            out.at[idx, "download_status"] = "download_error"
            out.at[idx, "quality_status"] = "download_error"
            continue
        content = result.get("content") or b""
        if isinstance(content, str):
            content = content.encode("utf-8")
        rel_path = report_storage_path(row, url)
        full_path = root / rel_path
        ensure_dir(full_path.parent)
        full_path.write_bytes(content)
        out.at[idx, "file_path"] = str(rel_path)
        out.at[idx, "file_hash"] = hashlib.sha256(content).hexdigest()
        out.at[idx, "download_status"] = "downloaded"
        out.at[idx, "quality_status"] = "downloaded_not_parsed"
        out.at[idx, "url_status"] = "ok"
        out.at[idx, "http_status"] = result.get("http_status")
        out.at[idx, "content_type"] = result.get("content_type") or out.at[idx, "content_type"]
        out.at[idx, "content_length"] = len(content)
        out.at[idx, "final_url"] = result.get("final_url") or url
        out.at[idx, "url_checked_at"] = now
        out.at[idx, "updated_at"] = now
        downloaded += 1

    write_parquet(report_index_path, out)
    summary = {
        "audit_status": audit_status,
        "eligible_reports": int(len(out) - skipped_not_ok - skipped_non_file_ok),
        "reports_downloaded": int(downloaded),
        "download_failed": int(failed),
        "skipped_not_ok": int(skipped_not_ok),
        "skipped_non_file_ok": int(skipped_non_file_ok),
        "errors": errors,
        "generated_at": now,
    }
    summary_path = root / "data" / "audited_download_summary.json"
    ensure_dir(summary_path.parent)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def is_downloadable_report_file(row: pd.Series) -> bool:
    url = str(row.get("source_url") or "")
    path = urlparse(url).path.lower()
    content_type = str(row.get("content_type") or "").lower()
    if path.endswith((".pdf", ".xls", ".xlsx", ".csv")):
        return True
    return any(marker in content_type for marker in ("pdf", "spreadsheet", "excel"))


def report_storage_path(row: pd.Series, url: str) -> Path:
    ticker = safe_path_part(str(row.get("ticker") or "UNKNOWN").upper())
    period = safe_path_part(str(row.get("period") or row.get("fiscal_year") or "unknown"))
    filename = safe_path_part(Path(urlparse(url).path).name or f"{row.get('report_id')}.bin")
    return Path("data") / "raw_reports" / ticker / period / filename


def safe_path_part(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", value.strip())
    return cleaned.strip("._") or "unknown"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--audit-status", default="ok")
    args = parser.parse_args()
    summary = download_audited_reports(Path(args.repo_root), audit_status=args.audit_status)
    print(
        "[download-audited-reports] "
        f"eligible={summary['eligible_reports']} downloaded={summary['reports_downloaded']} "
        f"failed={summary['download_failed']} skipped_non_file_ok={summary['skipped_non_file_ok']}"
    )
    return 0 if summary["download_failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
