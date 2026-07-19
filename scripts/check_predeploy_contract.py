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


# обязательные (broken → block) и опциональные (broken → только пометка)
REQUIRED = {
    "data.json": check_data,
    "marketsaw.json": check_marketsaw,
    "quality.json": check_quality,
    "dividend_calendar.json": check_dividend_calendar,
    "events_calendar.json": check_events_calendar,
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
        reason = checker(obj)
        results[name] = {"status": "broken" if reason else "ok", "reason": reason, "required": True}
        if reason:
            broken_required.append(name)
    for name in OPTIONAL:
        obj, e = load(name)
        results[name] = {"status": "broken" if e else "ok", "reason": e, "required": False}

    for name, r in results.items():
        tag = "OK " if r["status"] == "ok" else "BROKEN"
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
