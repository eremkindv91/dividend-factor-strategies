#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0 hard pre-deploy gate: пустой/битый core-JSON НЕ должен попасть на gh-pages.

Отличие от validate_site_data.py: тот трактует ОТСУТСТВУЮЩИЙ файл как warning
(«пропуск»), поэтому пустой data.json прошёл бы. Здесь — жёсткие обязательные
инварианты монетизируемого сайта: если они нарушены, exit 2 → публикация не идёт
(либо сработает restore_last_good_site_data.py и подменит битый файл last-good).

Проверяет минимальный контракт КАЖДОГО обязательного блока и печатает per-file
вердикт (OK / BROKEN + причина). Чистый stdlib.

CLI:
  python scripts/check_predeploy_contract.py            # все обязательные блоки
  python scripts/check_predeploy_contract.py --json     # + машиночитаемый итог в stdout
Коды выхода: 0 — все OK; 2 — есть BROKEN обязательный блок.
"""
from __future__ import annotations

import json
import math
import os
import sys
from datetime import date, datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(REPO, "site")


def load(name: str):
    """(obj, error|None). Отсутствие файла — это ошибка контракта (в отличие от validate_site_data)."""
    full = os.path.join(SITE, name)
    if not os.path.exists(full):
        return None, "файл отсутствует"
    if os.path.getsize(full) == 0:
        return None, "файл пуст (0 байт)"
    try:
        with open(full, encoding="utf-8") as f:
            return json.load(f), None
    except Exception as e:  # noqa: BLE001
        return None, f"невалидный JSON ({str(e)[:60]})"


def _age_days(s) -> float | None:
    if not s:
        return None
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds() / 86400
    except ValueError:
        try:
            return (date.today() - date.fromisoformat(str(s)[:10])).days
        except ValueError:
            return None


# ── контракты обязательных блоков (broken → блокирует публикацию) ─────────────
def check_data(d) -> str | None:
    meta, tk = d.get("meta") or {}, d.get("tickers")
    if not isinstance(tk, list) or len(tk) < 30:
        return f"tickers пуст/короткий ({len(tk) if isinstance(tk, list) else 'нет'})"
    if not meta.get("price_asof"):
        return "нет meta.price_asof"
    with_price = sum(1 for t in tk if isinstance(t.get("price"), (int, float)))
    if with_price < len(tk) * 0.5:
        return f"цены есть лишь у {with_price}/{len(tk)} тикеров (<50%)"
    return None


def check_marketsaw(d) -> str | None:
    if (d.get("index") or "").upper() != "MCFTR":
        return f"index={d.get('index')!r}, ожидался MCFTR (не IMOEX)"
    series = d.get("series") or []
    if len(series) < 100:
        return f"series слишком короткий ({len(series)})"
    if not d.get("current_phase"):
        return "нет current_phase"
    return None


def check_quality(d) -> str | None:
    meta, rows = d.get("meta") or {}, d.get("rows")
    if meta.get("methodology_version") != "ru_quality_sector_v2":
        return f"methodology_version={meta.get('methodology_version')!r}"
    if not isinstance(rows, list) or len(rows) < 30:
        return f"rows пуст/короткий ({len(rows) if isinstance(rows, list) else 'нет'})"
    if meta.get("n_universe") != len(rows):
        return "meta.n_universe не совпадает с rows"
    if any(row.get("eligible") and row.get("confidence") == "low" for row in rows):
        return "low-confidence компания попала в default eligible"
    expected = {
        "industrial_core": {"roe", "debt_to_equity", "earnings_variability"},
        "bank_quality": {"bank_roe", "capital_headroom", "bank_profit_variability"},
        "it_quality": {"ebitda_margin", "fcf_margin", "net_debt_to_ebitda"},
    }
    if set(expected) - {row.get("quality_model") for row in rows}:
        return "не все секторные Quality-модели представлены"
    for row in rows:
        model = row.get("quality_model")
        if model not in expected or set((row.get("raw") or {}).keys()) != expected[model]:
            return f"factor contract нарушен у {row.get('ticker')}"
        if model == "bank_quality" and (row.get("provenance") or {}).get("source_type") != "CBR_official_forms_102_123_135":
            return f"bank_quality без CBR provenance у {row.get('ticker')}"
    return None


def check_dividend_calendar(d) -> str | None:
    try:
        from validate_dividend_calendar import validate_payload
        errors = validate_payload(d)
        return errors[0] if errors else None
    except Exception as exc:  # noqa: BLE001
        return f"validator error: {str(exc)[:80]}"


def check_events_calendar(d) -> str | None:
    meta, events = d.get("meta") or {}, d.get("events")
    if meta.get("timezone") != "Europe/Moscow" or not meta.get("generated_at"):
        return "нет timezone/generated_at"
    if not isinstance(events, list):
        return "events не массив"
    if meta.get("event_count") != len(events):
        return "meta.event_count не совпадает с events"
    return None


def _dates_are_strict(rows: list, *, date_index: int = 0) -> tuple[list[str], str | None]:
    dates = [str(row[date_index])[:10] for row in rows if isinstance(row, list) and len(row) > date_index]
    if len(dates) != len(rows):
        return dates, "ряд содержит строки без даты"
    if len(dates) != len(set(dates)):
        return dates, "даты ряда не уникальны"
    if dates != sorted(dates):
        return dates, "даты ряда не возрастают"
    return dates, None


def check_market_history(d):
    instruments = {row.get("id"): row for row in d.get("instruments") or [] if isinstance(row, dict)}
    imoex = instruments.get("IMOEX") or {}
    rows = imoex.get("series") or []
    if not rows:
        return "IMOEX series пуст"
    dates, error = _dates_are_strict(rows)
    if error:
        return f"IMOEX: {error}"
    actual = dates[-1]
    if imoex.get("data_last") != actual:
        return "IMOEX data_last не совпадает с max(series dates)"
    all_latest = [str(row.get("data_last"))[:10] for row in instruments.values() if row.get("data_last")]
    if d.get("data_asof") != max(all_latest, default=None):
        return "data_asof не совпадает с instrument data_last"
    if d.get("daily_history_asof") not in (None, actual):
        return "daily_history_asof не совпадает с IMOEX completed history"
    current = imoex.get("current_session") or {}
    if current and str(current.get("date") or "") <= actual:
        return "current_session подменяет или дублирует completed daily row"
    if imoex.get("fallback_used"):
        if imoex.get("live_fetch_status") != "failed" or not imoex.get("fallback_source"):
            return "IMOEX fallback metadata противоречивы"
        lag = imoex.get("lag_trading_sessions")
        if lag is None or lag <= 1:
            return ("degraded", "IMOEX использует валидный last-good в пределах SLA")
        return f"IMOEX last-good отстаёт на {lag} торговых сессий"
    if imoex.get("live_fetch_status") not in (None, "success"):
        return "IMOEX live_fetch_status не подтверждает live ряд"
    return None


def _position_rows(entry: dict) -> tuple[list[str], str | None]:
    arrays = [entry.get(key) or [] for key in ("dates", "long", "short", "net")]
    if not arrays[0] or len({len(values) for values in arrays}) != 1:
        return [], "массивы dates/long/short/net пусты или разной длины"
    dates = [str(day)[:10] for day in arrays[0]]
    if dates != sorted(set(dates)):
        return dates, "даты не уникальны/не возрастают"
    if any(net != long_value - short_value
           for long_value, short_value, net in zip(arrays[1], arrays[2], arrays[3])):
        return dates, "net != long - short"
    return dates, None


def check_futures_positions(d):
    meta = d.get("meta") or {}
    imoex = ((d.get("indices") or {}).get("IMOEX") or {})
    dates, error = _position_rows(imoex)
    if error:
        return f"IMOEX: {error}"
    actual = dates[-1]
    if (imoex.get("summary") or {}).get("as_of") != actual:
        return "IMOEX summary.as_of не совпадает с max(dates)"
    if imoex.get("latest_observation_date") != actual:
        return "IMOEX latest_observation_date не совпадает с max(dates)"
    if imoex.get("data_asof") not in (None, actual):
        return "IMOEX data_asof не совпадает с max(dates)"
    if meta.get("as_of") and meta.get("as_of") < actual:
        return "meta.as_of старее IMOEX ряда"
    if (imoex.get("pagination") or {}).get("complete") is False:
        return "IMOEX pagination incomplete"
    entries = list((d.get("tickers") or {}).values()) + list((d.get("indices") or {}).values())
    failed = sum(row.get("update_status") == "failed" for row in entries)
    if entries and failed >= max(3, math.ceil(len(entries) * 0.8)):
        return f"массовый transport failure {failed}/{len(entries)}"
    if meta.get("calendar_status") == "unavailable":
        if imoex.get("expected_trading_date") is not None or imoex.get("lag_trading_sessions") is not None:
            return "calendar unavailable, но expected/lag заявлены"
    if imoex.get("fallback_used"):
        return ("degraded", "IMOEX использует валидный cache fallback")
    return None


def check_positioning(d):
    meta = d.get("meta") or {}
    analysis = meta.get("analysis_date") or meta.get("as_of")
    positions, positions_error = load("futures_positions.json")
    history, history_error = load("market_history.json")
    if positions_error or history_error:
        return "не удалось прочитать source artifacts для positioning"
    index = ((positions.get("indices") or {}).get("IMOEX") or {})
    position_dates = [str(day)[:10] for day in index.get("dates") or []]
    instrument = next((row for row in history.get("instruments") or [] if row.get("id") == "IMOEX"), {})
    price_dates = [str(row[0])[:10] for row in instrument.get("series") or [] if row and row[4] is not None]
    common = sorted(set(position_dates) & set(price_dates))
    if not common or analysis != common[-1]:
        return "analysis_date не равна max(position_dates ∩ price_dates)"
    if meta.get("position_latest") != max(position_dates, default=None):
        return "position_latest не совпадает с source"
    if meta.get("price_latest") not in (None, max(price_dates, default=None)):
        return "price_latest не совпадает с source"
    facts = ((d.get("IMOEX") or {}).get("facts") or {})
    pos_index = position_dates.index(analysis)
    long_value, short_value = index["long"][pos_index], index["short"][pos_index]
    if facts.get("long") != long_value or facts.get("short") != short_value \
            or facts.get("net") != long_value - short_value:
        return "facts рассчитаны не на analysis_date"
    if meta.get("status") == "degraded":
        return ("degraded", "общий срез валиден, один из source использует fallback/lag")
    return None


def is_broken_result(result) -> bool:
    return isinstance(result, str)


# обязательные (broken → block) и опциональные (broken → только пометка)
REQUIRED = {
    "data.json": check_data,
    "marketsaw.json": check_marketsaw,
    "quality.json": check_quality,
    "dividend_calendar.json": check_dividend_calendar,
    "events_calendar.json": check_events_calendar,
    "market_history.json": check_market_history,
    "futures_positions.json": check_futures_positions,
    "market_positioning_commentary.json": check_positioning,
}
OPTIONAL = [
    "marlamov.json",
    "bonds/screener.json",
    "cbr/valuation.json",
    "news.json",
    "alfa-index.json",
    "alfa-index-history.json",
    "site_financials.json",
]


def main() -> int:
    results = {}
    broken_required = []
    for name, checker in REQUIRED.items():
        obj, e = load(name)
        if e:
            results[name] = {"status": "broken", "reason": e, "required": True}
            broken_required.append(name)
            continue
        result = checker(obj)
        degraded = isinstance(result, tuple) and result[0] == "degraded"
        reason = result[1] if degraded else result
        status = "degraded" if degraded else "broken" if reason else "ok"
        results[name] = {"status": status, "reason": reason, "required": True}
        if is_broken_result(result):
            broken_required.append(name)
    for name in OPTIONAL:
        obj, e = load(name)
        results[name] = {"status": "broken" if e else "ok", "reason": e, "required": False}

    for name, r in results.items():
        tag = "OK " if r["status"] == "ok" else "DEGRADED" if r["status"] == "degraded" else "BROKEN"
        req = "" if r["required"] else " (опц.)"
        sys.stdout.write(f"  [{tag}] {name}{req}{(' — ' + r['reason']) if r['reason'] else ''}\n")

    if "--json" in sys.argv:
        sys.stdout.write("PREDEPLOY_JSON:" + json.dumps(results, ensure_ascii=False) + "\n")

    if broken_required:
        sys.stderr.write(f"\n[predeploy] БЛОК ПУБЛИКАЦИИ: битые обязательные блоки: {', '.join(broken_required)}\n")
        return 2
    sys.stdout.write("[predeploy] контракт обязательных блоков OK — публикация разрешена\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
