from __future__ import annotations

import argparse
import hashlib
import html as html_lib
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pandas as pd

from src.extraction.document_classifier import classify_document
from src.io_utils import stable_id, utc_now_iso, write_csv, write_parquet
from src.paths import REPO_ROOT, ensure_dir


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
    download: bool = False,
    metadata_checker=None,
    page_fetcher=None,
    downloader=None,
    max_child_links: int | None = 40,
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
        report_like = src["source_type"].isin([
            "manual_report",
            "report_pdf",
            "report_xlsx",
            "ifrs_report",
            "financial_reports_page",
            "report_page",
        ])
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
            if (
                not no_network
                and str(r.get("source_type", "")).strip() in {"financial_reports_page", "report_page"}
                and meta.get("ok")
                and "html" in str(meta.get("content_type") or "").lower()
            ):
                try:
                    fetcher = page_fetcher or fetch_page_html
                    html = fetcher(meta.get("final_url") or source_url)
                    discovered = discover_report_links(
                        html,
                        base_url=meta.get("final_url") or source_url,
                        source_row=r.to_dict(),
                        now=now,
                        from_year=from_year,
                        to_year=to_year,
                    )
                    if max_child_links is not None and max_child_links > 0:
                        discovered = discovered[:max_child_links]
                    for child in discovered:
                        child_meta = (
                            metadata_checker(child["source_url"])
                            if metadata_checker
                            else check_url_metadata(child["source_url"], timeout=5)
                        )
                        if not child_meta.get("ok"):
                            errors.append({
                                "ticker": str(r.get("ticker", "")).strip().upper(),
                                "source_type": "official_page_link",
                                "source_url": child["source_url"],
                                "issue": "url_metadata_error",
                                "detail": str(child_meta.get("error") or child_meta.get("http_status") or ""),
                                "created_at": now,
                            })
                            continue
                        child["http_status"] = child_meta.get("http_status")
                        child["content_type"] = child_meta.get("content_type") or ""
                        child["content_length"] = child_meta.get("content_length")
                        child["final_url"] = child_meta.get("final_url") or child["source_url"]
                        child["url_checked_at"] = now
                        child["url_status"] = "ok"
                        child["quality_status"] = "source_url_ok"
                        rows.append(child)
                except Exception as e:  # noqa: BLE001
                    errors.append({
                        "ticker": str(r.get("ticker", "")).strip().upper(),
                        "source_type": r.get("source_type", ""),
                        "source_url": source_url,
                        "issue": "report_page_parse_error",
                        "detail": str(e),
                        "created_at": now,
                    })

    out = pd.DataFrame(rows, columns=REPORT_COLUMNS).drop_duplicates("report_id") if rows else pd.DataFrame(columns=REPORT_COLUMNS)
    reports_downloaded = 0
    if download and not no_network and not out.empty:
        out, reports_downloaded, download_errors = download_report_files(root, out, downloader=downloader, now=now)
        errors.extend(download_errors)
    write_parquet(path, out)
    write_csv(root / "data" / "manual_review" / "report_source_errors.csv", errors, SOURCE_ERROR_COLUMNS)
    return {
        "reports_found": int(len(out)),
        "reports_downloaded": reports_downloaded,
        "source_errors": len(errors),
        "mode": "metadata_only_no_network" if no_network else "metadata_pending_download",
    }


def download_report_files(root: Path, reports: pd.DataFrame, downloader=None, now: str | None = None) -> tuple[pd.DataFrame, int, list[dict]]:
    now = now or utc_now_iso()
    out = reports.copy()
    errors: list[dict] = []
    downloaded = 0
    fetcher = downloader or download_url
    for idx, row in out.iterrows():
        if not should_download_report(row):
            continue
        url = str(row.get("source_url") or "").strip()
        try:
            result = fetcher(url)
        except Exception as e:  # noqa: BLE001
            result = {"ok": False, "error": str(e), "content": b"", "http_status": None}
        if not result.get("ok"):
            errors.append({
                "ticker": row.get("ticker"),
                "source_type": row.get("source_name", ""),
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
        file_hash = hashlib.sha256(content).hexdigest()
        rel_path = report_storage_path(row, url)
        full_path = root / rel_path
        ensure_dir(full_path.parent)
        full_path.write_bytes(content)
        out.at[idx, "file_path"] = str(rel_path)
        out.at[idx, "file_hash"] = file_hash
        out.at[idx, "download_status"] = "downloaded"
        out.at[idx, "quality_status"] = "downloaded_not_parsed"
        out.at[idx, "url_status"] = "ok"
        out.at[idx, "http_status"] = result.get("http_status")
        out.at[idx, "content_type"] = result.get("content_type") or out.at[idx, "content_type"]
        out.at[idx, "content_length"] = len(content)
        out.at[idx, "final_url"] = result.get("final_url") or url
        out.at[idx, "url_checked_at"] = now
        downloaded += 1
    return out, downloaded, errors


def should_download_report(row: pd.Series) -> bool:
    if str(row.get("document_type", "")).lower() == "presentation":
        return False
    if str(row.get("source_name", "")) not in {"official_page_link", "manual_report", "report_pdf", "report_xlsx", "ifrs_report"}:
        return False
    url = str(row.get("source_url") or "")
    path = urlparse(url).path.lower()
    return path.endswith((".pdf", ".xls", ".xlsx", ".csv", ".html", ".htm"))


def report_storage_path(row: pd.Series, url: str) -> Path:
    ticker = safe_path_part(str(row.get("ticker") or "UNKNOWN").upper())
    period = safe_path_part(str(row.get("period") or row.get("fiscal_year") or "unknown"))
    filename = safe_path_part(Path(urlparse(url).path).name or f"{row.get('report_id')}.bin")
    return Path("data") / "raw_reports" / ticker / period / filename


def safe_path_part(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", value.strip())
    return cleaned.strip("._") or "unknown"


def download_url(url: str, timeout: int = 30) -> dict:
    import requests

    headers = {"User-Agent": "dividend-factor-strategies/report-download"}
    resp = requests.get(url, allow_redirects=True, timeout=timeout, headers=headers)
    return {
        "ok": 200 <= resp.status_code < 400,
        "content": resp.content,
        "content_type": resp.headers.get("content-type", ""),
        "final_url": resp.url,
        "http_status": int(resp.status_code),
        "error": "" if 200 <= resp.status_code < 400 else f"HTTP {resp.status_code}",
    }


class LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict] = []
        self._current: dict | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attr = {k.lower(): v for k, v in attrs}
        href = attr.get("href")
        if href:
            self._current = {
                "href": href,
                "text": " ".join(str(attr.get(k) or "") for k in ("title", "aria-label", "download")).strip(),
            }

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current["text"] += data

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._current is not None:
            self.links.append(self._current)
            self._current = None


def discover_report_links(
    html: str,
    base_url: str,
    source_row: dict,
    now: str,
    from_year: int | None = None,
    to_year: int | None = None,
) -> list[dict]:
    html = html or ""
    parser = LinkExtractor()
    parser.feed(html)
    out: list[dict] = []
    seen: set[str] = set()
    raw_links = parser.links + extract_context_links(html)
    for link in raw_links:
        href = str(link.get("href") or "").strip()
        text = " ".join(str(link.get("text") or "").split())
        full_url = urljoin(base_url, href)
        dedup_key = normalize_report_url(full_url)
        if dedup_key in seen or not is_report_link(full_url, text):
            continue
        seen.add(dedup_key)
        fiscal_year = infer_fiscal_year(f"{text} {full_url}")
        if from_year is not None and fiscal_year is not None and fiscal_year < from_year:
            continue
        if to_year is not None and fiscal_year is not None and fiscal_year > to_year:
            continue
        classification = classify_document(title=text, text_sample=full_url)
        doc_type = classification["document_type"]
        reporting_standard = infer_reporting_standard(f"{text} {full_url}", doc_type)
        ticker = str(source_row.get("ticker", "")).strip().upper()
        report_id = stable_id("report-link", ticker, fiscal_year, reporting_standard, full_url)
        out.append({
            "report_id": report_id,
            "ticker": ticker,
            "inn": source_row.get("inn", ""),
            "company_name": source_row.get("company_name", ""),
            "period": str(fiscal_year) if fiscal_year else "",
            "period_type": "annual" if fiscal_year and "annual" in doc_type.lower() else "unknown",
            "fiscal_year": fiscal_year,
            "publication_date": "",
            "document_title": text or full_url.rsplit("/", 1)[-1],
            "document_type": doc_type,
            "reporting_standard": reporting_standard,
            "file_path": "",
            "source_url": full_url,
            "source_name": "official_page_link",
            "file_hash": "",
            "download_status": "link_discovered",
            "parse_status": "not_parsed",
            "extraction_status": "not_extracted",
            "quality_status": "link_discovered",
            "url_status": "discovered",
            "http_status": None,
            "content_type": "",
            "content_length": None,
            "final_url": full_url,
            "url_checked_at": "",
            "created_at": now,
            "updated_at": now,
        })
    return out


def normalize_report_url(url: str) -> str:
    parsed = urlparse(url)
    query = "" if parsed.query.lower() in {"dl=1", "download=1"} else parsed.query
    return parsed._replace(query=query, fragment="").geturl()


def extract_context_links(html: str) -> list[dict]:
    out: list[dict] = []
    for match in re.finditer(r"""href\s*=\s*["']([^"']+)["']""", html or "", flags=re.I):
        href = html_lib.unescape(match.group(1))
        out.append({"href": href, "text": link_context(html, match.start())})
    return out


def link_context(html: str, pos: int) -> str:
    starts = [html.rfind(tag, 0, pos) for tag in ("<tr", "<li", "<div", "<p")]
    start = max([s for s in starts if s >= 0], default=max(0, pos - 300))
    ends = [html.find(tag, pos) for tag in ("</tr>", "</li>", "</div>", "</p>")]
    end = min([e + len("</div>") for e in ends if e >= 0], default=min(len(html), pos + 300))
    fragment = html[start:end]
    fragment = re.sub(r"<script[\s\S]*?</script>", " ", fragment, flags=re.I)
    fragment = re.sub(r"<style[\s\S]*?</style>", " ", fragment, flags=re.I)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return " ".join(html_lib.unescape(fragment).split())


def is_report_link(url: str, text: str = "") -> bool:
    hay = f"{url} {text}".lower()
    if any(ext in urlparse(url).path.lower() for ext in (".pdf", ".xls", ".xlsx", ".html", ".htm")):
        return any(marker in hay for marker in (
            "ifrs", "мсфо", "rsbu", "рсбу", "financial", "statements", "отчет", "отчёт",
            "отчетность", "финансов", "presentation", "презентац", "results", "annual",
            "consolidated", "databook", "fy", "4q", "3q", "2q", "1q",
        ))
    return False


def infer_fiscal_year(text: str) -> int | None:
    years = [int(y) for y in re.findall(r"(?<!\d)(20[1-3]\d)(?!\d)", text or "")]
    return max(years) if years else None


def infer_reporting_standard(text: str, document_type: str) -> str:
    hay = f"{text} {document_type}".lower()
    if "ifrs" in hay or "мсфо" in hay:
        return "IFRS"
    if "rsbu" in hay or "рсбу" in hay or "ras" in hay:
        return "RAS"
    return "UNKNOWN"


def fetch_page_html(url: str, timeout: int = 15) -> str:
    import requests

    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(url, allow_redirects=True, timeout=timeout, headers=headers)
    resp.raise_for_status()
    return resp.text


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
        if resp.status_code in {403, 404, 405} or not (200 <= resp.status_code < 400):
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
    parser.add_argument("--download", action="store_true", help="Download direct discovered report files into data/raw_reports.")
    parser.add_argument("--allow-network", action="store_true", help="Allow URL metadata checks and report-page link discovery.")
    parser.add_argument("--max-child-links", type=int, default=40, help="Max report links to verify per report page; use 0 for no cap.")
    args = parser.parse_args()
    res = fetch_reports(
        Path(args.repo_root),
        from_year=args.from_year,
        to_year=args.to_year,
        ticker=args.ticker,
        limit_companies=args.limit_companies,
        no_network=args.no_network or (not args.allow_network and not args.download),
        download=args.download,
        max_child_links=None if args.max_child_links == 0 else args.max_child_links,
    )
    print(f"[fetch-reports] reports_downloaded={res['reports_downloaded']} mode={res['mode']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
