#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Forward-источник КОРПОРАТИВНЫХ событий (не дивидендных): календарь SmartLab.

Зачем: events_config.EVENT_IMPORTANCE описывает 10 типов событий, но пайплайн наполнял
только три (отсечка, последний день покупки, заседание ЦБ). Отчётности, ГОСА/ВОСА и
советы директоров — пустые слоты, хотя для дивидендного инвестора это события первого
порядка: именно СД рекомендует дивиденд, а ГОСА его утверждает.

Официальный портал раскрытий (e-disclosure.ru) для скрейпинга недоступен — JS-challenge
анти-бот отдаёт CI пустую заглушку (см. fetch_smartlab_dividends.py). SmartLab-календарь
агрегирует те же анонсы и уже принят в проекте как discovery-слой, поэтому события
помечаются source='smartlab' / data_status='announced' → фронт рисует чип «анонс,
сверьте у эмитента» и НЕ выдаёт их за подтверждённый факт.

Фильтр календаря работает только POST-ом (GET-параметры страница игнорирует).
Тикер берётся из ссылки на форум (<a href="/forum/AKRN">) — это канонический код бумаги,
надёжнее, чем префикс в тексте описания.

Чистый stdlib (urllib + regex), чтобы не тянуть bs4 в CI update.yml.

CLI: python scripts/fetch_smartlab_events.py [дней_вперёд]
"""
from __future__ import annotations

import html as html_mod
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta

URL = "https://smart-lab.ru/calendar/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0 Safari/537.36")
WAF_MARKERS = ("if you are not a bot", "spinner-loader", "get_cookie_spsn", "access denied", "captcha")

COUNTRY_RU = "0"

# тип календаря SmartLab → event_type проекта (events_config.EVENT_IMPORTANCE).
# Дивидендные отсечки (stocks_otsechka) СОЗНАТЕЛЬНО не берём: их уже даёт
# fetch_smartlab_dividends.py вместе с расчётом «купить до» через settlement-ядро.
CALENDAR_TYPES = {
    "company_reports": "company_earnings",
    "stocks_gosa": "gosa",
    "stocks_dirs": "board_dividend_recommendation",
    "stocks_other": "general_corporate_event",
}


def _strip(fragment: str) -> str:
    return re.sub(r"\s+", " ", html_mod.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _abs_url(href: str) -> str | None:
    """Ссылку вида /r.php?u=<urlencoded> разворачиваем в первоисточник; относительные — в абсолютные."""
    if not href:
        return None
    href = html_mod.unescape(href.strip())
    redirect = re.match(r"^/?r\.php\?.*\bu=([^&]+)", href)
    if redirect:
        target = urllib.parse.unquote(redirect.group(1))
        if target.startswith(("http://", "https://")):
            return target
    if href.startswith(("http://", "https://")):
        return href
    return urllib.parse.urljoin("https://smart-lab.ru", href)


def _refine_type(sl_type: str, description: str) -> str:
    """Уточнить тип по тексту: ярлыки в UI обещают конкретику, вешать их наугад нельзя."""
    text = (description or "").lower()
    if sl_type == "stocks_gosa":
        return "vosa" if ("воса" in text or "внеочередн" in text) else "gosa"
    if sl_type == "stocks_dirs":
        # EVENT_LABELS зовёт этот тип «Совет директоров (дивиденды)» — не ставим его
        # на СД с недивидендной повесткой (созыв ВОСА, стратегия и т.п.).
        return "board_dividend_recommendation" if "дивиденд" in text else "general_corporate_event"
    return CALENDAR_TYPES[sl_type]


def parse_events(page: str, sl_type: str) -> list[dict]:
    """HTML календаря → [{date, time_msk, ticker, description, url, event_type}]."""
    if any(marker in page.lower() for marker in WAF_MARKERS):
        return []
    rows: list[dict] = []
    for table in re.findall(r"<table[^>]*>.*?</table>", page, re.S | re.I):
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S | re.I):
            tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S | re.I)
            if len(tds) < 3:
                continue
            stamp = _strip(tds[0])
            match = re.match(r"(\d{2})\.(\d{2})\.(\d{4})(?:\s+(\d{1,2}:\d{2}))?", stamp)
            if not match:
                continue
            day, month, year, clock = match.groups()
            description = _strip(tds[2])
            if not description:
                continue
            ticker = None
            forum = re.search(r'href="/forum/([A-Z][A-Z0-9]{0,9})"', tds[2])
            if forum:
                ticker = forum.group(1).upper()
            if ticker:  # «AKRN: ГОСА …» / «SVCB - МСФО …» → тикер и так показан отдельной колонкой
                description = re.sub(rf"^{re.escape(ticker)}\s*[:\-–—]\s*", "", description, flags=re.I)
            link = re.search(r'href="([^"]+)"', tds[3]) if len(tds) > 3 else None
            rows.append({
                "date": f"{year}-{month}-{day}",
                "time_msk": clock,
                "ticker": ticker,
                "description": description,
                "url": _abs_url(link.group(1)) if link else None,
                "event_type": _refine_type(sl_type, description),
            })
    return rows


def _http_post(sl_type: str, start: date, end: date, retries: int = 3, timeout: int = 20) -> str:
    payload = urllib.parse.urlencode({
        "type": sl_type, "country": COUNTRY_RU,
        "from": start.strftime("%d.%m.%Y"), "to": end.strftime("%d.%m.%Y"),
        "apply_filter": "Найти",
    }).encode()
    last = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(URL, data=payload, headers={
                "User-Agent": UA, "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ru-RU,ru;q=0.9",
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": URL})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"SmartLab-календарь недоступен ({sl_type}): {last}")


def fetch_events(start: date, end: date, tickers: set | None = None) -> list[dict]:
    """Скачать календарь по всем типам. Недоступность одного типа не роняет остальные."""
    collected: dict[tuple, dict] = {}
    for sl_type in CALENDAR_TYPES:
        try:
            page = _http_post(sl_type, start, end)
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"[smartlab-events] {exc}\n")
            continue
        for row in parse_events(page, sl_type):
            if tickers and row["ticker"] and row["ticker"] not in tickers:
                continue
            collected.setdefault((row["ticker"], row["date"], row["event_type"]), row)
    return sorted(collected.values(), key=lambda row: (row["date"], row["ticker"] or "", row["event_type"]))


if __name__ == "__main__":
    horizon = int(sys.argv[1]) if len(sys.argv) > 1 else 21
    today = date.today()
    data = fetch_events(today, today + timedelta(days=horizon))
    sys.stderr.write(f"[smartlab-events] {len(data)} событий на {horizon} дней вперёд\n")
    for row in data:
        clock = f" {row['time_msk']}" if row["time_msk"] else ""
        print(f"  {row['date']}{clock}  {(row['ticker'] or '—'):8s} {row['event_type']:30s} {row['description'][:60]}")
