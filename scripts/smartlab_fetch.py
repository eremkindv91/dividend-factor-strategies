#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Детерминированный фетч годовой финансовой отчётности с smart-lab.ru (без API).

Назначение: ПРОДОЛЖЕНИЕ вперёд замороженной фундаментальной панели (новый отчётный
год), НЕ пересбор истории. Источник — публичные годовые страницы smart-lab.ru
(/q/{TICKER}/f/y/), данные годовые по МСФО, из реальных отчётов эмитента.

Почему парсер, а не LLM: страница — статичный HTML с машиночитаемой разметкой
строк (<tr field="revenue">…) и колонок-лет. LLM-чтение путает столбцы (даты
публикации ≠ финансовый год) — здесь привязка к настоящему заголовку «…2025 LTM».

ВАЖНО (этика/устойчивость): тянуть ТОЧЕЧНО — свои тикеры, новый год, с паузами,
для личного/академического использования, со ссылкой на источник (report_url).
Не массовый краулинг всей базы (ToS + блок).

Сверять с панелью обязательно (год-перехлёст): см. порог в reconcile-скрипте.
Колонки revenue/assets — надёжны; net_income/equity — с флагом; debt/ebitda
расходятся по аренде МСФО-16 и требуют маппинга, а не слепого копирования.

CLI:  python scripts/smartlab_fetch.py SBER LKOH CHMF
"""
from __future__ import annotations

import re
import sys
import time
import urllib.request
from typing import Dict, List, Optional

BASE = "https://smart-lab.ru/q/{ticker}/f/y/"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Сопоставление полей SmartLab → колонки панели (data/panels_final/panel_russia_final.csv).
# Деньги в SmartLab — млрд ₽; в панели — млн ₽ (×1000 при записи).
FIELD_MAP = {
    "revenue": "revenue_mln",
    "net_income": "net_profit_mln",
    "ebitda": "ebitda_mln",         # ⚠ аренда МСФО-16 — сверять, не копировать вслепую
    "assets": "assets_mln",
    "net_assets": "equity_mln",
    "debt": "total_debt_mln",       # ⚠ включает лизинг — расходится с панелью
    "net_debt": "net_debt_mln",     # ⚠ то же
    "ocf": "CFO_",
    "capex": "CAPEX_",
    "fcf": "FCF",
    "eps": None,
    "roe": "roe_pct",
    "roa": "roa_pct",
}
# Переименования тикеров: SmartLab знает страницу под НОВЫМ тикером.
ALIASES = {
    "YNDX": "YDEX",   # Yandex N.V. → МКПАО «Яндекс»
    "TCSG": "T",      # TCS Group → Т-Технологии
    "LNTA": "LENT",   # Лента
}

RELIABLE = ("revenue", "assets", "net_income")   # безопасно для авто-заполнения пустых (сверено)
# Только отчёт, НЕ авто-заполнять (определения расходятся): equity — доля меньшинства;
# debt/net_debt/ebitda — аренда МСФО-16.
NEEDS_MAPPING = ("net_assets", "debt", "net_debt", "ebitda")


def _http_get(url: str, timeout: int = 30, retries: int = 3) -> str:
    last: Optional[Exception] = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept-Language": "ru,en;q=0.9",
                "Accept-Encoding": "identity"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"smart-lab недоступен ({url}): {last}")


def _txt(c: str) -> str:
    s = re.sub(r"<[^>]+>", "", c)
    for ch in ("\xa0", " ", " "):
        s = s.replace(ch, " ")
    return s.replace("&nbsp;", " ").strip()


def _num(c: str) -> Optional[float]:
    s = _txt(c).replace("−", "-").replace(" ", "")
    if s in ("", "-", "—", "n/a"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _cells(body: str) -> List[str]:
    """<td>-ячейки строки, кроме служебных (иконка графика, спейсер LTM)."""
    return [c for a, c in re.findall(r"<td([^>]*)>(.*?)</td>", body, re.S)
            if "chartrow" not in a and "ltm_spc" not in a]


def _col_labels(html: str) -> List[str]:
    """Заголовок-годы: первая строка с 'LTM' и ≥3 годами (НЕ строка дат публикации)."""
    for m in re.finditer(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        labs = re.findall(r"20\d\d|LTM", _txt(m.group(1)))
        if "LTM" in labs and sum(l != "LTM" for l in labs) >= 3:
            return labs
    return []


def fetch_fundamentals(ticker: str) -> dict:
    """
    Вернуть {"ticker", "labels":[годы…,'LTM'], "data": {field: {год: значение}},
             "report_url", "source"}.  Деньги — млрд ₽ (как на странице).
    """
    ticker = ticker.strip().upper()
    req_ticker = ALIASES.get(ticker, ticker)   # фетчим под новым тикером, отдаём под исходным
    source = BASE.format(ticker=req_ticker)
    html = _http_get(source)
    labels = _col_labels(html)
    if not labels:
        raise RuntimeError(f"{ticker}: не найден заголовок-год (страница пуста/изменилась?)")

    data: Dict[str, Dict[str, Optional[float]]] = {}
    meta_rows: Dict[str, List[str]] = {}
    for m in re.finditer(r'<tr field="([^"]+)">(.*?)</tr>', html, re.S):
        field, body = m.group(1), m.group(2)
        vals = _cells(body)
        if field in ("report_url", "year_report_url", "currency", "date"):
            meta_rows[field] = [_txt(v) for v in vals]
        else:
            data[field] = dict(zip(labels, [_num(v) for v in vals]))

    report_url = ""
    for r in meta_rows.get("year_report_url", []) + meta_rows.get("report_url", []):
        mm = re.search(r"https?://\S+", r)
        if mm:
            report_url = mm.group(0)
            break

    currency = ""   # валюта отчётности (RUB/USD…) — критично: часть эмитентов в USD (RUAL и др.)
    for c in meta_rows.get("currency", []):
        if c.strip():
            currency = c.strip().upper()
            break

    return {"ticker": ticker, "labels": labels, "data": data, "currency": currency,
            "report_url": report_url, "source": source}


def get_many(tickers: List[str], delay: float = 1.0) -> Dict[str, dict]:
    """Батч с вежливой паузой между запросами. Ошибки — в поле 'error', не падаем."""
    out: Dict[str, dict] = {}
    for i, tk in enumerate(tickers):
        try:
            out[tk] = fetch_fundamentals(tk)
        except Exception as e:  # noqa: BLE001
            out[tk] = {"ticker": tk, "error": str(e)}
        if i + 1 < len(tickers):
            time.sleep(delay)
    return out


def _cli() -> int:
    tickers = [t.upper() for t in sys.argv[1:]] or ["SBER", "LKOH", "CHMF"]
    for tk in tickers:
        try:
            r = fetch_fundamentals(tk)
        except Exception as e:  # noqa: BLE001
            print(f"{tk}: ОШИБКА — {e}")
            continue
        print(f"\n=== {tk} | колонки: {r['labels']} | отчёт: {r['report_url'] or '—'} ===")
        for f in ("revenue", "net_income", "ebitda", "assets", "net_assets", "debt"):
            if f in r["data"]:
                last = {y: r["data"][f].get(y) for y in r["labels"][-3:]}
                print(f"  {f:11s} {last}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
