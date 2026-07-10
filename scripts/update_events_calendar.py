#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сборка site/events_calendar.json — календарь важных событий рынка РФ.

Только реальные источники (ничего синтетического, никакой генерации нейросетью):
  • Дивидендные даты — MOEX ISS (scripts/moex_iss.fetch_dividends → registryclosedate):
      – dividend_registry_close (дата отсечки/фиксации реестра);
      – last_buy_day = отсечка − 1 торговый день (режим расчётов T+1 на MOEX).
  • Заседания ЦБ по ключевой ставке — официальный график (scripts/events_config.CBR_RATE_MEETINGS,
    source_url = cbr.ru/dkp/cal_mp), помечены source=cbr_schedule / data_status=scheduled.

Горизонт вперёд — FORWARD_DAYS дней (наполняет «Ближайшие события»). Фильтр «сегодня»/«выходные»
делает фронт по фактической дате МСК — так вчерашние события НЕ показываются как сегодняшние,
даже если деплой отстал. Устойчивость к недоступности MOEX: кэш model_output/dividends_cache.json
(fallback + честный статус). Чистый stdlib + локальный moex_iss.

CLI: python scripts/update_events_calendar.py [--limit N] [--out site/events_calendar.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import moex_iss  # noqa: E402  (локальный модуль scripts/)
from events_config import (  # noqa: E402
    BACK_DAYS, CBR_RATE_MEETINGS, CBR_SOURCE_URL, DATA_QUALITY_PENALTY, EVENT_IMPORTANCE,
    EVENT_LABELS, FORWARD_DAYS, MCAP_BONUS_MAX, URGENCY_BONUS_TODAY,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(REPO, "site")
CACHE = os.path.join(REPO, "model_output", "dividends_cache.json")
MSK = timezone(timedelta(hours=3))
FAIL_BREAK = 15   # circuit-breaker: столько подряд ошибок MOEX → дальше только кэш


def clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def prev_business_day(d: date) -> date:
    """Предыдущий торговый день (пн–пт; праздники РФ не учитываются — приближение)."""
    x = d - timedelta(days=1)
    while x.weekday() >= 5:   # 5=сб, 6=вс
        x -= timedelta(days=1)
    return x


def load_universe():
    with open(os.path.join(SITE, "data.json"), encoding="utf-8") as f:
        d = json.load(f)
    uni = {}
    for t in d.get("tickers", []):
        tk = t.get("ticker")
        if tk:
            uni[tk] = {"name": t.get("name") or tk, "mcap": t.get("mcap"),
                       "price": t.get("price"), "dy": t.get("dividend_yield_expected")}
    return uni


def mcap_percentiles(uni):
    caps = sorted(v["mcap"] for v in uni.values() if isinstance(v.get("mcap"), (int, float)) and v["mcap"] > 0)
    if not caps:
        return {}
    out = {}
    for tk, v in uni.items():
        m = v.get("mcap")
        if isinstance(m, (int, float)) and m > 0:
            below = sum(1 for c in caps if c <= m)
            out[tk] = below / len(caps)
    return out


def load_cache():
    try:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_cache(cache):
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except OSError:
        pass


def composite_importance(base, mcap_pct, days_ahead, data_status):
    score = base + MCAP_BONUS_MAX * (mcap_pct or 0)
    if days_ahead == 0:
        score += URGENCY_BONUS_TODAY
    if data_status != "fresh":
        score -= DATA_QUALITY_PENALTY
    return int(round(clamp(score)))


def build_dividend_events(uni, pct, today, start, horizon, cache):
    """Возвращает (events, live_ok, cache_used, broken). Тянет MOEX ISS с кэш-фолбэком."""
    events, live_ok, cache_used, consecutive_fail = [], 0, 0, 0
    tickers = sorted(uni.keys())
    for tk in tickers:
        rows, status = None, "fresh"
        if consecutive_fail < FAIL_BREAK:
            try:
                rows = moex_iss.fetch_dividends(tk)
                cache[tk] = rows
                live_ok += 1
                consecutive_fail = 0
                time.sleep(0.03)   # вежливый пейсинг
            except (RuntimeError, Exception):  # noqa: BLE001 — сеть/ISS: деградируем к кэшу
                consecutive_fail += 1
        if rows is None:                       # ошибка или сработал circuit-breaker
            if tk in cache:
                rows, status, cache_used = cache[tk], "stale", cache_used + 1
            else:
                continue
        for d_str, dps in rows:
            try:
                rc = date.fromisoformat(str(d_str)[:10])
            except ValueError:
                continue
            if not (start <= rc <= horizon):
                continue
            info = uni[tk]
            yld = None
            if isinstance(info.get("price"), (int, float)) and info["price"] > 0 and isinstance(dps, (int, float)):
                yld = dps / info["price"] * 100
            ytxt = f" (≈{yld:.1f}% к цене)" if yld is not None else ""
            mp = pct.get(tk, 0.0)
            # событие: закрытие реестра (отсечка)
            events.append(_ev(
                f"{tk}-{rc.isoformat()}-registry", rc, tk, info["name"], "dividend_registry_close",
                f"Дивиденд ₽{dps:.2f} на акцию{ytxt}. После отсечки цена обычно корректируется на "
                f"величину дивиденда (дивидендный гэп).",
                composite_importance(EVENT_IMPORTANCE["dividend_registry_close"], mp, (rc - today).days, status),
                "moex_iss", "https://www.moex.com/", status))
            # событие: последний день покупки «с дивидендом» (T+1)
            lbd = prev_business_day(rc)
            if start <= lbd <= horizon:
                events.append(_ev(
                    f"{tk}-{rc.isoformat()}-lastbuy", lbd, tk, info["name"], "last_buy_day",
                    f"Последний торговый день, покупка в который попадает в реестр под дивиденд "
                    f"₽{dps:.2f}{ytxt} (режим расчётов T+1). Дата отсечки — {rc.isoformat()}.",
                    composite_importance(EVENT_IMPORTANCE["last_buy_day"], mp, (lbd - today).days, status),
                    "moex_iss", "https://www.moex.com/", status))
    broken = (live_ok == 0 and cache_used == 0)
    return events, live_ok, cache_used, broken


def build_cbr_events(start, horizon):
    events = []
    for m in CBR_RATE_MEETINGS:
        try:
            md = date.fromisoformat(m["date"])
        except (ValueError, KeyError):
            continue
        if not (start <= md <= horizon):
            continue
        key = " (опорное, со среднесрочным прогнозом)" if m.get("is_key") else ""
        events.append(_ev(
            f"CBR-{md.isoformat()}-rate", md, None, "Банк России", "cbr_rate_decision",
            f"Заседание Совета директоров Банка России по ключевой ставке{key}. Решение и пресс-релиз "
            f"обычно публикуются в 13:30 МСК; влияет на весь рынок (акции, облигации, ОФЗ).",
            EVENT_IMPORTANCE["cbr_rate_decision"], "cbr_schedule", CBR_SOURCE_URL, "scheduled"))
    return events


def _ev(eid, d, ticker, company, etype, desc, importance, source, url, data_status):
    title = EVENT_LABELS.get(etype, (etype, "Событие"))[0]
    return {"id": eid, "date": d.isoformat(), "time_msk": None, "ticker": ticker, "company": company,
            "event_type": etype, "title": title, "description": desc, "importance": int(importance),
            "portfolio_relevant": False, "source": source, "source_url": url, "data_status": data_status}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="ограничить число тикеров (для теста)")
    ap.add_argument("--out", default=os.path.join(SITE, "events_calendar.json"))
    args = ap.parse_args()

    now = datetime.now(MSK)
    today = now.date()
    start = today - timedelta(days=BACK_DAYS)   # окно назад для понедельничного catch-up
    horizon = today + timedelta(days=FORWARD_DAYS)
    uni = load_universe()
    if args.limit:
        uni = {k: uni[k] for k in sorted(uni)[:args.limit]}
    pct = mcap_percentiles(uni)
    cache = load_cache()

    div_events, live_ok, cache_used, broken = build_dividend_events(uni, pct, today, start, horizon, cache)
    save_cache(cache)
    cbr_events = build_cbr_events(start, horizon)
    events = div_events + cbr_events
    # сортировка: по важности (убыв.), затем по дате (возр.)
    events.sort(key=lambda e: (-e["importance"], e["date"]))

    today_iso = today.isoformat()
    today_count = sum(1 for e in events if e["date"] == today_iso)
    fallback = (live_ok == 0 and cache_used > 0)
    status = "broken" if (broken and not cbr_events) else ("fallback" if fallback else "fresh")
    sources = set(e["source"] for e in events)

    payload = {
        "meta": {
            "generated_at": now.replace(microsecond=0).isoformat(),
            "timezone": "Europe/Moscow",
            "status": status,
            "source_count": len(sources),
            "sources": sorted(sources),
            "event_count": len(events),
            "today_event_count": today_count,
            "forward_days": FORWARD_DAYS,
            "moex_live_ok": live_ok,
            "moex_cache_used": cache_used,
            "fallback": fallback,
            "disclaimer": "События из открытых источников (MOEX ISS, график ЦБ РФ). Информационно, "
                          "не индивидуальная инвестиционная рекомендация. Даты сверяйте с эмитентом/биржей.",
        },
        "events": events,
    }
    os.makedirs(SITE, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    sys.stderr.write(f"[events] {len(events)} событий (сегодня {today_count}); "
                     f"MOEX live={live_ok} cache={cache_used}; status={status}; out={args.out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
