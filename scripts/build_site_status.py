#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0: генерирует site/site_status.json — единый статус свежести данных для UI Data Health.

Для каждого блока сайта считает {status, asof, age_days, note}:
  fresh     — данные свежие (возраст в пределах порога блока);
  stale     — данные старше порога (публикуем, но помечаем);
  fallback  — файл подменён last-good (см. restore_last_good_site_data.py → site/_fallback.json);
  broken    — файл отсутствует/битый/провалил контракт.

Один битый блок не роняет остальные: каждый считается независимо. Чистый stdlib.
Запускать ПОСЛЕ сборок и restore, ПЕРЕД публикацией. Выход всегда 0 (это не гейт, а витрина).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(REPO, "site")

# блок → (человекочитаемое имя, файл, поля-даты по приоритету, порог свежести в днях)
# порог свежести в ДНЯХ (дробный). Честный SLA: старое НЕ должно выглядеть свежим.
# Дневные блоки — 1.6 дня (покрывает разрыв пт→пн); новости — 0.25 дня (6ч): пропущенный
# утренний прогон должен быть виден как stale до открытия, а не маскироваться суточным SLA.
BLOCKS = [
    ("market", "Цены и мультипликаторы", "data.json", ["meta.price_asof", "meta.generated_at"], 1.6),
    ("marketsaw", "Фаза рынка (MCFTR)", "marketsaw.json", ["data_last", "generated_at"], 1.6),
    ("market_history", "Графики рынка (MOEX ISS)", "market_history.json", ["generated_at"], 1.6),
    ("returns", "История доходностей", "returns.json", ["meta.asof"], 45),
    ("marlamov", "Форвардная дивдоходность", "marlamov.json", ["meta.updated", "meta.asof", "meta.generated_at"], 3),
    ("bonds", "Облигации", "bonds/screener.json", ["meta.data_date", "meta.updated", "meta.generated_at"], 1.6),
    ("cbr", "Банки РФ (ЦБ)", "cbr/valuation.json", ["meta.moex_asof", "meta.generated_at"], 1.6),
    ("news", "Новости", "news.json", ["generated_at", "date"], 0.25),
    ("events", "События дня", "events_calendar.json", ["meta.generated_at"], 1.6),
    ("financials", "Фундамент", "site_financials.json", ["meta.generated_at"], 45),
]


def load(name: str):
    full = os.path.join(SITE, name)
    if not os.path.exists(full) or os.path.getsize(full) == 0:
        return None
    try:
        with open(full, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return "broken"


def dig(obj, path: str):
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def age_days(s) -> float | None:
    if not s:
        return None
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds() / 86400
    except ValueError:
        try:
            return float((date.today() - date.fromisoformat(str(s)[:10])).days)
        except ValueError:
            return None


def main() -> int:
    # список файлов, подменённых last-good (пометка fallback)
    fb = load("_fallback.json") or {}
    fallback_files = set((fb.get("restored") or [])) if isinstance(fb, dict) else set()

    blocks = {}
    worst = "fresh"
    order = {"fresh": 0, "stale": 1, "fallback": 2, "broken": 3}
    for key, title, fname, date_fields, thresh in BLOCKS:
        obj = load(fname)
        asof, age = None, None
        if obj is None or obj == "broken":
            status = "broken"
            note = "файл отсутствует" if obj is None else "битый JSON"
        else:
            for f in date_fields:
                v = dig(obj, f)
                if v:
                    asof = str(v)[:19]
                    age = age_days(v)
                    break
            if fname in fallback_files:
                status, note = "fallback", "подставлены последние успешные данные"
            elif age is None:
                status, note = "stale", "нет даты в meta"
            elif age > thresh:
                status, note = "stale", f"возраст {age:.0f} дн (> {thresh})"
            else:
                status, note = "fresh", ""
        blocks[key] = {"title": title, "status": status, "asof": asof,
                       "age_days": round(age, 1) if age is not None else None, "note": note}
        if order[status] > order[worst]:
            worst = status

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall": worst,
        "blocks": blocks,
        "disclaimer": "Информационно-аналитический материал. Не индивидуальная инвестиционная рекомендация. "
                      "Все данные с источником и датой; fallback-данные помечены.",
    }
    out = os.path.join(SITE, "site_status.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    sys.stderr.write(f"[site-status] overall={worst}; " +
                     ", ".join(f"{k}:{v['status']}" for k, v in blocks.items()) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
