from __future__ import annotations

import html as html_lib
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlparse

import pandas as pd

from src.extraction.document_classifier import classify_document
from src.io_utils import stable_id, utc_now_iso
from src.paths import REPO_ROOT


DISCLOSURE_ERROR_COLUMNS = [
    "ticker", "inn", "company_name", "source_type", "source_url", "issue", "detail", "created_at",
]

PAGE_SOURCE_TYPES = {"disclosure_page", "financial_reports_page", "report_page", "issuer_website"}
DIRECT_REPORT_SOURCE_TYPES = {"manual_report", "report_pdf", "report_xlsx", "ifrs_report"}
REPORT_SOURCE_TYPES = PAGE_SOURCE_TYPES | DIRECT_REPORT_SOURCE_TYPES


def normalize_issuer_name(name: str) -> str:
    s = (name or "").lower().replace("\xa0", " ")
    for token in (
        "публичное акционерное общество", "акционерное общество", "пао", "ао", "мкпао",
        "гмк", "гк", "pjsc", "public joint stock company", "joint stock company", "jsc",
        "plc", "ltd", "inc", "corp", "corporation", "company", "co",
        '"', "'", "«", "»", "(", ")",
    ):
        s = s.replace(token, " ")
    s = re.sub(r"[^0-9a-zа-яё]+", " ", s)
    return " ".join(s.split())


def discover_disclosure_reports(
    root: Path = REPO_ROOT,
    from_year: int | None = None,
    to_year: int | None = None,
    ticker: str | None = None,
    limit_companies: int | None = None,
    no_network: bool = False,
    metadata_checker=None,
    page_fetcher=None,
    max_child_links: int | None = 40,
    checked_at: str | None = None,
) -> tuple[list[dict], list[dict]]:
    checked_at = checked_at or utc_now_iso()
    rows: list[dict] = []
    errors: list[dict] = []
    sources = load_report_sources(root)
    if ticker:
        sources = sources[sources["ticker"].astype(str).str.upper() == str(ticker).upper()]
    elif limit_companies is not None and limit_companies > 0 and "ticker" in sources:
        sources = limit_by_unique_tickers(sources, limit_companies)

    checker = metadata_checker or check_url_metadata
    fetcher = page_fetcher or fetch_page_html

    for _, source in sources.iterrows():
        source_row = source.to_dict()
        source_type = str(source_row.get("source_type", "")).strip()
        source_url = str(source_row.get("source_url", "")).strip()
        if not source_url:
            continue
        issue = validate_source_url(source_url)
        if issue:
            errors.append(error_row(source_row, issue, "", checked_at))
            continue

        fiscal_year = int_or_none(source_row.get("fiscal_year"))
        if fiscal_year is not None and from_year is not None and fiscal_year < from_year:
            continue
        if fiscal_year is not None and to_year is not None and fiscal_year > to_year:
            continue

        meta = unchecked_metadata(source_url)
        if not no_network:
            meta = safe_metadata_check(checker, source_url)
            if not meta.get("ok"):
                errors.append(error_row(source_row, "url_metadata_error", str(meta.get("error") or meta.get("http_status") or ""), checked_at))

        base = source_to_report_row(source_row, meta, checked_at, no_network=no_network)
        if source_type in DIRECT_REPORT_SOURCE_TYPES:
            if is_source_ifrs_report(source_row):
                rows.append(base)
            continue

        if source_type in PAGE_SOURCE_TYPES:
            rows.append(base)
            if no_network:
                continue
            if meta.get("ok") is False:
                continue
            if not looks_like_html(meta.get("content_type"), source_url):
                continue
            try:
                html = fetcher(meta.get("final_url") or source_url)
            except Exception as e:  # noqa: BLE001
                errors.append(error_row(source_row, "disclosure_page_fetch_error", str(e), checked_at))
                continue
            if is_disclosure_error_page(html):
                errors.append(error_row(source_row, "disclosure_error_page", "WAF/404/error page detected", checked_at))
                continue
            children = discover_report_links(
                html=html,
                base_url=meta.get("final_url") or source_url,
                source_row=source_row,
                checked_at=checked_at,
                from_year=from_year,
                to_year=to_year,
            )
            if max_child_links is not None and max_child_links > 0:
                children = children[:max_child_links]
            for child in children:
                child_meta = safe_metadata_check(checker, child["source_url"])
                if not child_meta.get("ok"):
                    errors.append(error_row(
                        {**source_row, "source_type": "official_page_link", "source_url": child["source_url"]},
                        "url_metadata_error",
                        str(child_meta.get("error") or child_meta.get("http_status") or ""),
                        checked_at,
                    ))
                    continue
                rows.append(apply_metadata(child, child_meta, checked_at))

    return dedupe_report_rows(rows), errors


def load_report_sources(root: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    source_path = root / "data" / "company_sources.csv"
    if source_path.exists():
        src = pd.read_csv(source_path, dtype=str).fillna("")
        if "active" in src:
            src = src[src["active"].astype(str).str.lower().isin(["", "true", "1", "yes"])]
        if "source_type" in src:
            src = src[src["source_type"].isin(REPORT_SOURCE_TYPES)]
        frames.append(src)

    registry_path = root / "data" / "companies_registry.csv"
    if registry_path.exists():
        reg = pd.read_csv(registry_path, dtype=str).fillna("")
        rows = []
        for _, r in reg.iterrows():
            common = {
                "ticker": str(r.get("ticker") or r.get("secid") or "").strip().upper(),
                "inn": r.get("inn", ""),
                "company_name": r.get("full_name") or r.get("short_name") or "",
                "document_title": "",
                "document_type": "financial_reports_page",
                "reporting_standard": "MIXED",
                "fiscal_year": "",
                "period": "",
                "period_type": "page",
                "priority": 9,
                "active": "true",
                "notes": "from_companies_registry",
            }
            if str(r.get("disclosure_page_url") or "").strip():
                rows.append({**common, "source_type": "disclosure_page", "source_url": r.get("disclosure_page_url")})
            if str(r.get("e_disclosure_company_id") or "").strip():
                rows.append({
                    **common,
                    "source_type": "disclosure_page",
                    "source_url": f"https://www.e-disclosure.ru/portal/company.aspx?id={str(r.get('e_disclosure_company_id')).strip()}",
                })
            if str(r.get("issuer_website") or "").strip():
                rows.append({**common, "source_type": "issuer_website", "source_url": r.get("issuer_website")})
        if rows:
            frames.append(pd.DataFrame(rows))

    if not frames:
        return pd.DataFrame(columns=[
            "ticker", "inn", "company_name", "source_type", "source_url", "document_title",
            "document_type", "reporting_standard", "fiscal_year", "period", "period_type",
            "priority", "active", "notes",
        ])
    out = pd.concat(frames, ignore_index=True, sort=False).fillna("")
    if out.empty:
        return out
    out["_dedupe"] = out.apply(lambda r: normalize_report_url(str(r.get("source_url") or "")), axis=1)
    out = out.drop_duplicates(["ticker", "source_type", "_dedupe"], keep="first").drop(columns=["_dedupe"])
    return out.reset_index(drop=True)


def limit_by_unique_tickers(src: pd.DataFrame, limit: int) -> pd.DataFrame:
    tickers: list[str] = []
    seen: set[str] = set()
    for raw in src["ticker"].astype(str):
        value = raw.strip().upper()
        if value and value not in seen:
            seen.add(value)
            tickers.append(value)
        if len(tickers) >= limit:
            break
    return src[src["ticker"].astype(str).str.upper().isin(set(tickers))]


def source_to_report_row(source_row: dict, meta: dict, checked_at: str, no_network: bool) -> dict:
    source_url = str(source_row.get("source_url") or "").strip()
    fy = int_or_none(source_row.get("fiscal_year"))
    source_type = str(source_row.get("source_type") or "").strip()
    title = source_row.get("document_title") or f"{source_row.get('ticker')} report {source_row.get('period') or fy or ''}".strip()
    url_ok = bool(meta.get("ok")) if not no_network else None
    status = "metadata_only" if no_network else ("metadata_checked" if url_ok else "url_error")
    return {
        "report_id": stable_id("report", source_row.get("ticker"), source_row.get("period"), source_row.get("reporting_standard"), source_url),
        "ticker": str(source_row.get("ticker", "")).strip().upper(),
        "inn": source_row.get("inn", ""),
        "company_name": source_row.get("company_name", ""),
        "period": source_row.get("period") or (str(fy) if fy is not None else ""),
        "period_type": source_row.get("period_type") or ("page" if source_type in PAGE_SOURCE_TYPES else "annual"),
        "fiscal_year": fy,
        "publication_date": "",
        "document_title": title,
        "document_type": source_row.get("document_type") or ("financial_reports_page" if source_type in PAGE_SOURCE_TYPES else "unknown"),
        "reporting_standard": source_row.get("reporting_standard") or ("MIXED" if source_type in PAGE_SOURCE_TYPES else "UNKNOWN"),
        "file_path": "",
        "source_url": source_url,
        "source_name": source_type or "manual_report",
        "file_hash": "",
        "download_status": status,
        "parse_status": "not_parsed",
        "extraction_status": "not_extracted",
        "quality_status": "not_checked" if no_network else ("source_url_ok" if url_ok else "source_url_error"),
        "url_status": "not_checked" if no_network else ("ok" if url_ok else "error"),
        "http_status": meta.get("http_status"),
        "content_type": meta.get("content_type") or "",
        "content_length": meta.get("content_length"),
        "final_url": meta.get("final_url") or source_url,
        "url_checked_at": "" if no_network else checked_at,
        "created_at": checked_at,
        "updated_at": checked_at,
    }


def discover_report_links(
    html: str,
    base_url: str,
    source_row: dict,
    checked_at: str,
    from_year: int | None = None,
    to_year: int | None = None,
) -> list[dict]:
    parser = LinkExtractor()
    parser.feed(html or "")
    out: list[dict] = []
    seen: set[str] = set()
    for link in parser.links + extract_context_links(html or ""):
        href = str(link.get("href") or "").strip()
        text = " ".join(str(link.get("text") or "").split())
        full_url = urljoin(base_url, href)
        dedup_key = normalize_report_url(full_url)
        if dedup_key in seen or not is_ifrs_report_link(full_url, text):
            continue
        seen.add(dedup_key)
        fiscal_year = infer_fiscal_year(f"{text} {full_url}")
        if from_year is not None and fiscal_year is not None and fiscal_year < from_year:
            continue
        if to_year is not None and fiscal_year is not None and fiscal_year > to_year:
            continue
        doc_type = infer_document_type(f"{text} {full_url}")
        ticker = str(source_row.get("ticker", "")).strip().upper()
        out.append({
            "report_id": stable_id("report-link", ticker, fiscal_year, "IFRS", full_url),
            "ticker": ticker,
            "inn": source_row.get("inn", ""),
            "company_name": source_row.get("company_name", ""),
            "period": str(fiscal_year) if fiscal_year else "",
            "period_type": "interim" if "interim" in doc_type.lower() else ("annual" if fiscal_year else "unknown"),
            "fiscal_year": fiscal_year,
            "publication_date": "",
            "document_title": text or full_url.rsplit("/", 1)[-1],
            "document_type": doc_type,
            "reporting_standard": "IFRS",
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
            "created_at": checked_at,
            "updated_at": checked_at,
        })
    return out


def is_ifrs_report_link(url: str, text: str = "") -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower()
    if not path.endswith((".pdf", ".xls", ".xlsx", ".html", ".htm")) and "fileload.ashx" not in path:
        return False
    hay = normalize_search_text(f"{url} {text}")
    if any(bad in hay for bad in ("presentation", "презентац", "press release", "пресс релиз", "rsbu", "рсбу", "ras ")):
        return False
    required = (
        "ifrs", "мсфо", "consolidated financial statements", "consolidated statements",
        "консолидированная финансовая отчетность", "консолидированной финансовой отчетности",
        "annual ifrs", "interim ifrs",
    )
    return any(marker in hay for marker in required)


def is_source_ifrs_report(source_row: dict) -> bool:
    standard = str(source_row.get("reporting_standard") or "").strip().upper()
    source_type = str(source_row.get("source_type") or "")
    hay = f"{source_row.get('source_url', '')} {source_row.get('document_title', '')} {source_row.get('document_type', '')}"
    if standard in {"IFRS", "МСФО"}:
        return True
    if source_type == "report_xlsx" and any(marker in normalize_search_text(hay) for marker in ("ifrs", "мсфо", "consolidated financial")):
        return True
    return is_ifrs_report_link(str(source_row.get("source_url") or ""), hay)


def infer_document_type(text: str) -> str:
    hay = normalize_search_text(text)
    if "interim" in hay or "6m" in hay or "3m" in hay or "9m" in hay or "q1" in hay or "q2" in hay or "q3" in hay:
        return "IFRS interim"
    if "annual" in hay or "годов" in hay or "12m" in hay or "4q" in hay:
        return "IFRS annual"
    classified = classify_document(title=text, text_sample=text)
    doc_type = classified.get("document_type") or ""
    return doc_type if "ifrs" in doc_type.lower() else "IFRS report"


def normalize_search_text(text: str) -> str:
    text = (text or "").lower().replace("\xa0", " ").replace("\u202f", " ")
    text = text.replace("ё", "е")
    return " ".join(text.split())


def is_disclosure_error_page(html: str) -> bool:
    hay = normalize_search_text(re.sub(r"<[^>]+>", " ", html or ""))
    markers = (
        "access denied", "forbidden", "waf", "captcha", "cloudflare", "error 404",
        "404", "страница не найдена", "доступ запрещен", "ошибка", "service unavailable",
    )
    return any(marker in hay for marker in markers)


@dataclass
class DisclosureClient:
    base_url: str = "https://www.e-disclosure.ru"
    page_fetcher: object | None = None
    sleep_seconds: float = 0.4

    def find_issuer_card(self, ticker: str = "", inn: str = "", issuer_name: str = "") -> dict | None:
        fetcher = self.page_fetcher or fetch_page_html
        for query, method in query_candidates(ticker, inn, issuer_name):
            if self.sleep_seconds:
                time.sleep(self.sleep_seconds)
            url = self.search_url(query)
            try:
                html = fetcher(url)
            except Exception:  # noqa: BLE001
                continue
            card = parse_issuer_card(html, self.base_url, ticker=ticker, inn=inn, issuer_name=issuer_name)
            if card:
                return {**card, "match_method": method}
        return None

    def search_url(self, query: str) -> str:
        return urljoin(self.base_url, f"/portal/company.aspx?attempt=1&keyword={quote_plus(query)}")


def query_candidates(ticker: str, inn: str, issuer_name: str) -> list[tuple[str, str]]:
    out = []
    if str(inn or "").strip():
        out.append((str(inn).strip(), "inn"))
    if str(ticker or "").strip():
        out.append((str(ticker).strip(), "ticker"))
    if str(issuer_name or "").strip():
        out.append((str(issuer_name).strip(), "issuer_name"))
    return out


def parse_issuer_card(html: str, base_url: str, ticker: str = "", inn: str = "", issuer_name: str = "") -> dict | None:
    parser = LinkExtractor()
    parser.feed(html or "")
    hay = normalize_search_text(re.sub(r"<[^>]+>", " ", html or ""))
    normalized_name = normalize_issuer_name(issuer_name)
    ticker_norm = normalize_search_text(ticker)
    inn_norm = normalize_search_text(inn)
    if inn_norm and inn_norm not in hay:
        inn_match = False
    else:
        inn_match = bool(inn_norm)
    name_match = bool(normalized_name and normalized_name in normalize_issuer_name(hay))
    ticker_match = bool(ticker_norm and re.search(rf"(^|[^a-z0-9]){re.escape(ticker_norm)}([^a-z0-9]|$)", hay))
    if not (inn_match or name_match or ticker_match):
        return None
    for link in parser.links:
        href = str(link.get("href") or "")
        if "company.aspx" in href.lower() or "emittent" in href.lower():
            return {
                "source_url": urljoin(base_url, href),
                "title": " ".join(str(link.get("text") or "").split()),
            }
    return None


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


def apply_metadata(row: dict, meta: dict, checked_at: str) -> dict:
    out = dict(row)
    out.update({
        "http_status": meta.get("http_status"),
        "content_type": meta.get("content_type") or "",
        "content_length": meta.get("content_length"),
        "final_url": meta.get("final_url") or row.get("source_url"),
        "url_checked_at": checked_at,
        "url_status": "ok",
        "quality_status": "source_url_ok",
        "updated_at": checked_at,
    })
    return out


def dedupe_report_rows(rows: list[dict]) -> list[dict]:
    out = []
    seen: set[str] = set()
    for row in rows:
        key = str(row.get("report_id") or normalize_report_url(str(row.get("source_url") or "")))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def error_row(source_row: dict, issue: str, detail: str, created_at: str) -> dict:
    return {
        "ticker": str(source_row.get("ticker", "")).strip().upper(),
        "inn": source_row.get("inn", ""),
        "company_name": source_row.get("company_name", ""),
        "source_type": source_row.get("source_type", ""),
        "source_url": source_row.get("source_url", ""),
        "issue": issue,
        "detail": detail,
        "created_at": created_at,
    }


def unchecked_metadata(url: str) -> dict:
    return {
        "url": url,
        "ok": None,
        "http_status": None,
        "content_type": "",
        "content_length": None,
        "final_url": url,
        "error": "",
    }


def safe_metadata_check(checker, url: str) -> dict:
    try:
        return checker(url)
    except Exception as e:  # noqa: BLE001
        return {**unchecked_metadata(url), "ok": False, "error": str(e)}


def looks_like_html(content_type: str | None, url: str) -> bool:
    ctype = str(content_type or "").lower()
    path = urlparse(url).path.lower()
    return "html" in ctype or path.endswith(("/", ".html", ".htm", ".aspx", ".php")) or "." not in Path(path).name


def normalize_report_url(url: str) -> str:
    parsed = urlparse(url)
    query = "" if parsed.query.lower() in {"dl=1", "download=1"} else parsed.query
    return parsed._replace(query=query, fragment="").geturl()


def validate_source_url(url: str) -> str | None:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        return "invalid_url_scheme"
    if not parsed.netloc:
        return "missing_url_host"
    return None


def infer_fiscal_year(text: str) -> int | None:
    years = [int(y) for y in re.findall(r"(?<!\d)(20[1-3]\d)(?!\d)", text or "")]
    return max(years) if years else None


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
            "content_length": int_or_none(content_length),
            "final_url": resp.url,
            "error": "",
        }
    except Exception as e:  # noqa: BLE001
        return {**unchecked_metadata(url), "ok": False, "error": str(e)}


def fetch_page_html(url: str, timeout: int = 15) -> str:
    import requests

    headers = {"User-Agent": "Mozilla/5.0 dividend-factor-strategies/report-discovery"}
    resp = requests.get(url, allow_redirects=True, timeout=timeout, headers=headers)
    resp.raise_for_status()
    return resp.text


def int_or_none(value) -> int | None:
    try:
        if value is None or pd.isna(value) or str(value).strip() == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None
