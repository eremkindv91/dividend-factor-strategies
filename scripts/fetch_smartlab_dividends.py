#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Forward-источник дивидендных отсечек: календарь SmartLab (smart-lab.ru/dividends/).

Зачем: единственный прежний источник событий — MOEX ISS /dividends.json — публикует запись
об отсечке ОКОЛО даты закрытия реестра, т.е. уже ПОСЛЕ дивидендного гэпа. SmartLab-календарь
содержит АНОНСИРОВАННЫЕ (из решений СД/ГОСА) даты «Купить До» и «Дата закрытия реестра»
ЗАРАНЕЕ → сайт может предупредить до гэпа. e-disclosure (официальный портал) для скрейпинга
недоступен: JS-challenge анти-бот отдаёт CI пустую заглушку.

ОДНА страница на запрос (не покомпанийный краул) → нет rate-limit проблемы reconcile-краула.
Чистый stdlib (urllib + regex), чтобы не тянуть bs4 в CI update.yml. Источник помечается
'smartlab'; downstream сверяет с MOEX и не выдаёт прогноз за подтверждённый факт.

CLI: python scripts/fetch_smartlab_dividends.py [tickers...]
"""
from __future__ import annotations

import re
import sys
import time
import urllib.error
import urllib.request

URL = "https://smart-lab.ru/dividends/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0 Safari/537.36")
WAF_MARKERS = ("if you are not a bot", "spinner-loader", "get_cookie_spsn", "access denied", "captcha")

# заголовок → каноническое поле (по подстроке, регистронезависимо)
COLMAP = [
    ("тикер", "ticker"),
    ("период", "period"),
    ("дивиденд", "dividend"),
    ("див. дох", "yield"),
    ("купить до", "buy_before"),
    ("закрытия реестра", "record_date"),
    ("выплата", "payment_date"),
]


def _strip(html_fragment: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html_fragment)).strip()


def _num(s: str):
    """'37,64' → 37.64; '13,5%' → 13.5; иначе None."""
    if not s:
        return None
    m = re.search(r"-?\d[\d\s]*(?:[.,]\d+)?", s.replace("\xa0", " "))
    if not m:
        return None
    try:
        return float(m.group(0).replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def _date(s: str):
    """'20.07.2026' → '2026-07-20'; иначе None."""
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", s or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def _pick_main_table(html: str) -> str | None:
    """Выбрать таблицу дивкалендаря: с колонкой «Дата закрытия реестра» и наибольшим числом строк."""
    best, best_rows = None, 0
    for t in re.findall(r"<table[^>]*>.*?</table>", html, re.S):
        head = " ".join(_strip(h) for h in re.findall(r"<th[^>]*>(.*?)</th>", t, re.S)).lower()
        if "закрытия реестра" not in head and "купить до" not in head:
            continue
        n = len(re.findall(r"<tr", t))
        if n > best_rows:
            best, best_rows = t, n
    return best


def parse_dividends(html: str) -> list[dict]:
    """HTML календаря → [{ticker, period, dividend, yield, buy_before, record_date, payment_date}].
    Маппинг колонок по заголовку (устойчив к перестановке/добавлению колонок)."""
    if any(m in html.lower() for m in WAF_MARKERS):
        return []
    table = _pick_main_table(html)
    if not table:
        return []
    headers = [_strip(h).lower() for h in re.findall(r"<th[^>]*>(.*?)</th>", table, re.S)]
    idx = {}
    for i, h in enumerate(headers):
        for needle, field in COLMAP:
            if needle in h and field not in idx:
                idx[field] = i
    if "ticker" not in idx or "record_date" not in idx:
        return []

    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if len(tds) <= idx["record_date"]:
            continue
        cells = [_strip(td) for td in tds]
        ticker = (cells[idx["ticker"]] or "").upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9]{1,9}", ticker):   # только валидные тикеры MOEX
            continue
        rec = _date(cells[idx["record_date"]])
        if not rec:
            continue
        def cell(field, conv=lambda x: x):
            i = idx.get(field)
            return conv(cells[i]) if (i is not None and i < len(cells)) else None
        out.append({
            "ticker": ticker,
            "period": cell("period"),
            "dividend": cell("dividend", _num),
            "yield": cell("yield", _num),
            "buy_before": cell("buy_before", _date),
            "record_date": rec,
            "payment_date": cell("payment_date", _date),
        })
    return out


def _http_get(url: str = URL, retries: int = 3, timeout: int = 20) -> str:
    last = None
    for a in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ru-RU,ru;q=0.9"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (a + 1))
    raise RuntimeError(f"SmartLab недоступен: {last}")


def fetch_dividends(tickers: set | None = None, url: str = URL) -> list[dict]:
    """Скачать и распарсить календарь. tickers — необязательный фильтр по универсуму."""
    try:
        html = _http_get(url)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[smartlab-div] {e}\n")
        return []
    rows = parse_dividends(html)
    if tickers:
        rows = [r for r in rows if r["ticker"] in tickers]
    return rows


if __name__ == "__main__":
    flt = {t.upper() for t in sys.argv[1:]} or None
    data = fetch_dividends(flt)
    sys.stderr.write(f"[smartlab-div] {len(data)} записей\n")
    for r in data[:30]:
        print(f"  {r['ticker']:8s} {r['record_date']}  купить до {r['buy_before']}  "
              f"{r['dividend']}₽  {r['yield']}%")
