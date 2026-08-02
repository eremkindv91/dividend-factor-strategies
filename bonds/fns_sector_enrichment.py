#!/usr/bin/env python3
"""Conservative issuer-sector enrichment from the official FNS public service."""
from __future__ import annotations

import time
import re
from io import BytesIO
from datetime import datetime, timezone
from typing import Callable, Iterable

import requests
from pypdf import PdfReader

FNS_BASE = "https://pb.nalog.ru"
FNS_SEARCH_PAGE = f"{FNS_BASE}/search.html"
FNS_SEARCH_ENDPOINT = f"{FNS_BASE}/search-proc.json"
FNS_SOURCE_URL = "https://pb.nalog.ru/"
EGRUL_BASE = "https://egrul.nalog.ru"
USER_AGENT = "dividend-site/bonds (+github.com/eremkindv91)"


class FNSRateLimitError(RuntimeError):
    """The official service refused further automated requests."""


def sector_from_okved(okved: str | None) -> str | None:
    """Map a verified primary OKVED code to the site's coarse sector taxonomy."""
    if not okved:
        return None
    try:
        section = int(str(okved).split(".", 1)[0])
    except ValueError:
        return None
    if section in {5, 7, 8}:
        return "Металлы и добыча"
    if section == 6 or section == 19 or str(okved).startswith("09.10"):
        return "Нефть и газ"
    if section == 9:
        return "Металлы и добыча"
    if 10 <= section <= 18 or 45 <= section <= 47 or 55 <= section <= 56:
        return "Потребительский сектор"
    if section == 20:
        return "Химия и нефтехимия"
    if section in {21, 86, 87, 88}:
        return "Здравоохранение"
    if 22 <= section <= 23 or 25 <= section <= 33:
        return "Промышленность"
    if section == 24:
        return "Сталь и цветная металлургия"
    if section == 35:
        return "Электроэнергетика"
    if 36 <= section <= 39:
        return "Коммунальные услуги"
    if 41 <= section <= 43:
        return "Строительство и девелопмент"
    if 49 <= section <= 53:
        return "Транспорт"
    if section == 61:
        return "Телекоммуникации"
    if section in {58, 59, 60, 62, 63}:
        return "Информационные технологии"
    if 64 <= section <= 66:
        return "Финансы"
    if section == 68:
        return "Недвижимость"
    if 69 <= section <= 82:
        return "Деловые услуги"
    if section == 84:
        return "Государственный сектор"
    if section == 85:
        return "Образование"
    if 90 <= section <= 96:
        return "Потребительские услуги"
    return None


def _request_json(session: requests.Session, method: str, url: str, **kwargs) -> dict:
    response = session.request(method, url, timeout=30, **kwargs)
    if response.status_code in {400, 403, 429}:
        body = response.text[:500]
        if "pbRateLimit" in body or response.status_code == 429:
            raise FNSRateLimitError("FNS rate limit reached")
        if "pbSearchCaptcha" in body or "captcha" in body.lower():
            raise FNSRateLimitError("FNS captcha required")
        raise FNSRateLimitError(f"FNS request blocked with HTTP {response.status_code}")
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("FNS returned a non-object payload")
    return payload


def lookup_company_by_inn(
    inn: str,
    *,
    session: requests.Session | None = None,
    sleep: Callable[[float], None] = time.sleep,
    poll_attempts: int = 6,
) -> dict:
    """Return one exact FNS company record; never accept a fuzzy/name match."""
    inn = str(inn or "").strip()
    if not (inn.isdigit() and len(inn) == 10):
        raise ValueError("A legal-entity INN must contain exactly 10 digits")
    own_session = session is None
    session = session or requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        if own_session:
            session.get(FNS_SEARCH_PAGE, timeout=30).raise_for_status()
        request = _request_json(
            session,
            "POST",
            FNS_SEARCH_ENDPOINT,
            data={
                "mode": "search-ul",
                "queryUl": inn,
                "page": "1",
                "pageSize": "10",
                "pbCaptchaToken": "",
                "token": "",
            },
        )
        if request.get("captchaRequired"):
            raise RuntimeError("FNS captcha required")
        request_id = request.get("id")
        if not request_id:
            raise RuntimeError("FNS search request id missing")
        result = None
        for attempt in range(poll_attempts):
            sleep(min(1.5 * (attempt + 1), 5.0))
            result = _request_json(
                session,
                "POST",
                FNS_SEARCH_ENDPOINT,
                data={"id": request_id, "method": "get-response"},
            )
            if result:
                break
        rows = (((result or {}).get("ul") or {}).get("data") or [])
        exact = [row for row in rows if str(row.get("inn") or "").strip() == inn]
        if len(exact) != 1:
            raise RuntimeError(f"FNS exact INN match count is {len(exact)}")
        row = exact[0]
        return {
            "inn": inn,
            "issuer_name": str(row.get("namep") or row.get("namec") or "").strip(),
            "ogrn": str(row.get("ogrn") or "").strip() or None,
            "okved_main": str(row.get("okved2main") or "").strip() or None,
            "okved_main_name": str(row.get("okved2mainname") or "").strip() or None,
            "okved_main_type": str(row.get("okved2maintype") or "").strip() or None,
            "source_url": FNS_SOURCE_URL,
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    finally:
        if own_session:
            session.close()


def _main_okved_from_extract_text(text: str) -> tuple[str, str] | None:
    marker = "Сведения об основном виде деятельности"
    start = text.find(marker)
    if start < 0:
        return None
    block = text[start:start + 1200]
    match = re.search(
        r"Код и наименование вида деятельности\s+(\d{2}(?:\.\d{1,2}){0,2})\s+(.+)",
        block,
    )
    if not match:
        return None
    return match.group(1), match.group(2).splitlines()[0].strip()


def lookup_company_by_inn_egrul(
    inn: str,
    *,
    session: requests.Session | None = None,
    sleep: Callable[[float], None] = time.sleep,
    poll_attempts: int = 12,
    maximum_pdf_bytes: int = 5_000_000,
) -> dict:
    """Read the main OKVED from an official EGRUL extract held only in memory."""
    inn = str(inn or "").strip()
    if not (inn.isdigit() and len(inn) == 10):
        raise ValueError("A legal-entity INN must contain exactly 10 digits")
    own_session = session is None
    session = session or requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    try:
        request = _request_json(session, "POST", f"{EGRUL_BASE}/", data={"query": inn})
        if request.get("captchaRequired"):
            raise FNSRateLimitError("EGRUL captcha required")
        search_token = request.get("t")
        if not search_token:
            raise RuntimeError("EGRUL search token missing")
        rows = []
        for attempt in range(poll_attempts):
            sleep(min(float(attempt + 1), 3.0))
            result = _request_json(session, "GET", f"{EGRUL_BASE}/search-result/{search_token}")
            if result.get("status") != "wait":
                rows = result.get("rows") or []
                break
        exact = [row for row in rows if str(row.get("i") or "").strip() == inn]
        if len(exact) != 1:
            raise RuntimeError(f"EGRUL exact INN match count is {len(exact)}")
        row = exact[0]
        extract_token = row.get("t")
        generation = _request_json(session, "GET", f"{EGRUL_BASE}/vyp-request/{extract_token}?r=")
        if generation.get("captchaRequired"):
            raise FNSRateLimitError("EGRUL extract captcha required")
        ready = False
        for attempt in range(poll_attempts):
            sleep(min(float(attempt + 1), 3.0))
            status = _request_json(session, "GET", f"{EGRUL_BASE}/vyp-status/{extract_token}")
            if status.get("status") == "ready":
                ready = True
                break
            if status.get("status") == "error":
                raise RuntimeError("EGRUL extract generation failed")
        if not ready:
            raise RuntimeError("EGRUL extract generation timed out")
        response = session.get(
            f"{EGRUL_BASE}/vyp-download/{extract_token}",
            headers={"Accept": "application/pdf,*/*"},
            timeout=30,
        )
        response.raise_for_status()
        if response.headers.get("content-type", "").split(";", 1)[0] != "application/pdf":
            raise RuntimeError("EGRUL extract is not a PDF")
        if len(response.content) > maximum_pdf_bytes:
            raise RuntimeError("EGRUL extract exceeds the in-memory size limit")
        reader = PdfReader(BytesIO(response.content))
        text = "\n".join((page.extract_text() or "") for page in reader.pages[:10])
        parsed = _main_okved_from_extract_text(text)
        if not parsed:
            raise RuntimeError("EGRUL main OKVED not found in extract")
        okved, okved_name = parsed
        return {
            "inn": inn,
            "issuer_name": str(row.get("n") or row.get("c") or "").strip(),
            "ogrn": str(row.get("o") or "").strip() or None,
            "okved_main": okved,
            "okved_main_name": okved_name,
            "okved_main_type": "egrul_extract",
            "source_url": f"{EGRUL_BASE}/index.html",
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    finally:
        if own_session:
            session.close()


def enrich_issuer_master(
    issuer_master: dict,
    candidates: Iterable[dict],
    *,
    lookup: Callable[[str], dict] | None = None,
    limit: int = 30,
    request_interval_seconds: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[dict, dict]:
    """Enrich a copy of issuer master; errors keep the issuer explicitly unknown."""
    enriched = {
        **issuer_master,
        "issuers": dict(issuer_master.get("issuers") or {}),
    }
    status = {"requested": 0, "mapped": 0, "unmapped": 0, "errors": []}
    status["resolved"] = []
    session = None
    if lookup is None:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        session.get(FNS_SEARCH_PAGE, timeout=30).raise_for_status()
        def lookup(inn: str) -> dict:
            try:
                return lookup_company_by_inn(inn, session=session, sleep=sleep)
            except FNSRateLimitError:
                return lookup_company_by_inn_egrul(inn, session=session, sleep=sleep)
    try:
        seen: set[str] = set()
        for candidate in candidates:
            inn = str(candidate.get("issuer_inn") or "").strip()
            if not inn or inn in seen or inn in enriched["issuers"]:
                continue
            if status["requested"] >= int(limit):
                break
            seen.add(inn)
            status["requested"] += 1
            try:
                record = lookup(inn)
                if record.get("inn") != inn:
                    raise RuntimeError("FNS response INN mismatch")
                sector = sector_from_okved(record.get("okved_main"))
                if not sector:
                    status["unmapped"] += 1
                else:
                    item = {
                        "issuer_name": record.get("issuer_name") or candidate.get("issuer_name"),
                        "sector": sector,
                        "sector_source": "fns_main_okved",
                        "sector_source_url": record.get("source_url") or FNS_SOURCE_URL,
                        "okved_main": record.get("okved_main"),
                        "okved_main_name": record.get("okved_main_name"),
                        "okved_main_type": record.get("okved_main_type"),
                        "checked_at": record.get("checked_at"),
                        "ultimate_parent_id": None,
                    }
                    enriched["issuers"][inn] = item
                    status["resolved"].append({"issuer_inn": inn, **item})
                    status["mapped"] += 1
            except FNSRateLimitError as exc:
                status["errors"].append({"issuer_inn": inn, "reason": str(exc)[:180]})
                status["stopped_early"] = True
                break
            except Exception as exc:  # noqa: BLE001 - source failure is diagnostic, not fatal
                status["errors"].append({"issuer_inn": inn, "reason": str(exc)[:180]})
            if request_interval_seconds > 0:
                sleep(request_interval_seconds)
    finally:
        if session is not None:
            session.close()
    status["status"] = "ok" if not status["errors"] else "partial"
    return enriched, status
