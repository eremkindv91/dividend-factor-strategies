from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

from src.paths import REPO_ROOT, ensure_dir


AUDIT_COLUMNS = [
    "ticker",
    "issuer_name",
    "document_title",
    "document_type",
    "reporting_standard",
    "fiscal_year",
    "period",
    "source_url",
    "document_url",
    "confidence",
    "discovery_method",
    "url_status",
    "looks_like_pdf_or_xlsx",
    "looks_like_official_report",
    "is_ifrs_candidate",
    "audit_status",
    "audit_reason",
]

REPORT_MARKERS = (
    "ifrs",
    "мсфо",
    "consolidated financial statements",
    "consolidated statements",
    "консолидированная финансовая отчетность",
    "консолидированной финансовой отчетности",
    "financial statements",
    "financial results",
    "financial reports",
    "финансовая отчетность",
    "отчетность",
    "results and reports",
    "group results",
    "consolidated_financial_statements",
)

IFRS_MARKERS = (
    "ifrs",
    "мсфо",
    "consolidated financial statements",
    "консолидированная финансовая отчетность",
    "консолидированной финансовой отчетности",
    "consolidated_financial_statements",
    "summary financials",
)

BAD_MARKERS = (
    "presentation",
    "презентац",
    "press release",
    "пресс-релиз",
    "press releases",
    "investor day",
    "sustainability",
    "esg",
    "annual report",
)

ERROR_MARKERS = (
    "access denied",
    "forbidden",
    "captcha",
    "cloudflare",
    "waf",
    "service unavailable",
    "страница не найдена",
    "доступ запрещен",
    "ошибка 404",
    "error 404",
    "not found",
)

OFFICIAL_HOST_MARKERS = (
    "e-disclosure.ru",
    "sberbank.com",
    "lukoil.com",
    "phosagro.com",
    "moex.com",
    "fs.moex.com",
    "mts.ru",
    "severstal.com",
    "nornickel.com",
    "nlmk.com",
    "novatek.ru",
    "polyus.com",
    "rosneft.com",
    "magnit.com",
    "aeroflot.ru",
    "storage.ir.mts.ru",
    "cdn.phosagro.com",
)


def audit_report_index(
    root: Path = REPO_ROOT,
    checker=None,
    generated_at: str | None = None,
) -> dict:
    report_index_path = root / "data" / "report_index.parquet"
    if not report_index_path.exists():
        raise FileNotFoundError("data/report_index.parquet is missing. Run fetch_reports first.")

    generated_at = generated_at or utc_now_iso()
    report_index = pd.read_parquet(report_index_path)
    check = checker or light_check_url
    rows = [audit_row(row.to_dict(), check) for _, row in report_index.iterrows()]
    audit = pd.DataFrame(rows, columns=AUDIT_COLUMNS)

    out_dir = ensure_dir(root / "results" / "disclosure")
    audit_path = out_dir / "report_index_audit.csv"
    summary_path = out_dir / "report_index_audit_summary.json"
    audit.to_csv(audit_path, index=False)
    summary = audit_summary(audit, generated_at)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def audit_row(row: dict, checker) -> dict:
    source_url = clean_str(row.get("source_url"))
    document_url = clean_str(row.get("document_url"))
    target_url = document_url or source_url
    meta = checker(target_url)
    classification = classify_link(row, target_url, meta)
    return {
        "ticker": row.get("ticker"),
        "issuer_name": row.get("issuer_name") or row.get("company_name"),
        "document_title": row.get("document_title"),
        "document_type": row.get("document_type"),
        "reporting_standard": row.get("reporting_standard"),
        "fiscal_year": row.get("fiscal_year"),
        "period": row.get("period"),
        "source_url": source_url,
        "document_url": document_url,
        "confidence": row.get("confidence", ""),
        "discovery_method": row.get("discovery_method") or row.get("source_name"),
        "url_status": classification["url_status"],
        "looks_like_pdf_or_xlsx": classification["looks_like_pdf_or_xlsx"],
        "looks_like_official_report": classification["looks_like_official_report"],
        "is_ifrs_candidate": classification["is_ifrs_candidate"],
        "audit_status": classification["audit_status"],
        "audit_reason": classification["audit_reason"],
    }


def classify_link(row: dict, target_url: str, meta: dict) -> dict:
    title = norm(row.get("document_title"))
    doc_type = norm(row.get("document_type"))
    standard = norm(row.get("reporting_standard"))
    url_text = norm(target_url)
    sample = norm(re.sub(r"<[^>]+>", " ", clean_str(meta.get("sample"))))
    hay = f"{title} {doc_type} {standard} {url_text} {sample}"
    host = urlparse(target_url).netloc.lower()
    http = meta.get("http_status")
    content_type = clean_str(meta.get("content_type"))

    looks_file = looks_like_pdf_or_xlsx(target_url, content_type)
    official_host = any(marker in host for marker in OFFICIAL_HOST_MARKERS)
    looks_report = any(marker in hay for marker in REPORT_MARKERS)
    is_ifrs = (
        "ifrs" in standard
        or "ifrs" in doc_type
        or any(marker in hay for marker in IFRS_MARKERS)
    ) and not any(marker in hay for marker in ("rsbu", "рсбу", "ras "))
    error_page = any(marker in sample for marker in ERROR_MARKERS)
    bad_kind = any(marker in hay for marker in BAD_MARKERS) and not is_ifrs
    synthetic = is_synthetic_url(target_url)

    if not target_url or synthetic:
        audit_status = "rejected"
        reason = "missing_or_synthetic_url"
    elif meta.get("status") == "timeout":
        audit_status = "failed"
        reason = f"timeout: {meta.get('error') or ''}".strip()
    elif meta.get("status") == "request_error":
        audit_status = "failed"
        reason = f"request_error: {meta.get('error') or ''}".strip()
    elif http in (403, 429) or (
        error_page and any(marker in sample for marker in ("access denied", "forbidden", "captcha", "waf", "cloudflare", "доступ запрещен"))
    ):
        audit_status = "blocked"
        reason = f"blocked_or_waf http={http}"
    elif http is None or http >= 500 or http == 404:
        audit_status = "failed"
        reason = f"http_error_or_error_page http={http}"
    elif 200 <= http < 400:
        if error_page:
            audit_status = "failed"
            reason = "error page detected despite HTTP success"
        elif bad_kind:
            audit_status = "rejected"
            reason = "presentation_or_press_release_not_ifrs"
        elif official_host and is_ifrs and (looks_file or looks_report):
            audit_status = "ok"
            reason = f"official host; IFRS/report markers; http={http}; content_type={content_type}"
        elif official_host and looks_report and not is_ifrs:
            audit_status = "needs_review"
            reason = f"official report page but IFRS markers not explicit; http={http}; content_type={content_type}"
        elif is_ifrs and looks_file:
            audit_status = "needs_review"
            reason = f"IFRS-looking file but host not allowlisted; http={http}; content_type={content_type}"
        else:
            audit_status = "rejected"
            reason = f"no reliable IFRS/report evidence; http={http}; content_type={content_type}"
    else:
        audit_status = "needs_review"
        reason = f"unhandled http={http}; checker_status={meta.get('status')}"

    return {
        "url_status": http if http is not None else meta.get("status"),
        "looks_like_pdf_or_xlsx": bool(looks_file),
        "looks_like_official_report": bool(official_host and looks_report and not error_page),
        "is_ifrs_candidate": bool(is_ifrs and not synthetic),
        "audit_status": audit_status,
        "audit_reason": reason,
    }


def audit_summary(audit: pd.DataFrame, generated_at: str) -> dict:
    counts = audit["audit_status"].value_counts().to_dict() if not audit.empty else {}
    doc_text = (audit["document_title"].fillna("") + " " + audit["document_type"].fillna("")).str.lower()
    return {
        "total_links": int(len(audit)),
        "ok": int(counts.get("ok", 0)),
        "blocked": int(counts.get("blocked", 0)),
        "failed": int(counts.get("failed", 0)),
        "rejected": int(counts.get("rejected", 0)),
        "needs_review": int(counts.get("needs_review", 0)),
        "unique_companies": int(audit["ticker"].dropna().astype(str).str.upper().nunique()) if not audit.empty else 0,
        "ifrs_candidates": int(audit["is_ifrs_candidate"].sum()) if not audit.empty else 0,
        "annual_ifrs_candidates": int(((audit["is_ifrs_candidate"]) & doc_text.str.contains("annual|годов", regex=True)).sum()) if not audit.empty else 0,
        "interim_ifrs_candidates": int(((audit["is_ifrs_candidate"]) & doc_text.str.contains("interim|6m|q1|q2|q3|9m|промеж", regex=True)).sum()) if not audit.empty else 0,
        "generated_at": generated_at,
    }


def light_check_url(url: str, timeout: int = 12) -> dict:
    if not url or norm(url) == "nan":
        return {"status": "missing", "http_status": None, "content_type": "", "final_url": "", "sample": "", "error": "missing url"}
    try:
        import requests

        headers = {"User-Agent": "dividend-factor-strategies/disclosure-audit"}
        response = requests.head(url, allow_redirects=True, timeout=timeout, headers=headers)
        status = int(response.status_code)
        content_type = response.headers.get("content-type", "")
        final_url = response.url
        sample = ""
        if status in {403, 405, 429} or status >= 400 or not content_type:
            response = requests.get(url, allow_redirects=True, timeout=timeout, headers={**headers, "Range": "bytes=0-4095"}, stream=True)
            status = int(response.status_code)
            content_type = response.headers.get("content-type", content_type)
            final_url = response.url
        if "html" in content_type.lower() or "text" in content_type.lower():
            try:
                sample = response.text[:4096]
            except Exception:  # noqa: BLE001
                sample = ""
        response.close()
        return {"status": "checked", "http_status": status, "content_type": content_type, "final_url": final_url, "sample": sample, "error": ""}
    except TimeoutError as e:
        return {"status": "timeout", "http_status": None, "content_type": "", "final_url": url, "sample": "", "error": str(e)}
    except Exception as e:  # noqa: BLE001
        message = str(e)
        status = "timeout" if "timed out" in message.lower() or "timeout" in message.lower() else "request_error"
        return {"status": status, "http_status": None, "content_type": "", "final_url": url, "sample": "", "error": message}


def looks_like_pdf_or_xlsx(url: str, content_type: str = "") -> bool:
    path = urlparse(url).path.lower()
    ctype = content_type.lower()
    return path.endswith((".pdf", ".xls", ".xlsx")) or any(marker in ctype for marker in ("pdf", "spreadsheet", "excel"))


def is_synthetic_url(url: str) -> bool:
    hay = norm(url)
    return any(marker in hay for marker in ("example.com", "{", "}", "<ticker>", "template"))


def norm(text: object) -> str:
    return " ".join(clean_str(text).lower().replace("\xa0", " ").replace("\u202f", " ").split())


def clean_str(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    return str(value).strip()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    args = parser.parse_args()
    summary = audit_report_index(Path(args.repo_root))
    print(
        "[disclosure-audit] "
        f"total={summary['total_links']} ok={summary['ok']} blocked={summary['blocked']} "
        f"failed={summary['failed']} rejected={summary['rejected']} needs_review={summary['needs_review']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
