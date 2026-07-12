#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Production-билдер «Помощника фазы рынка»: MCFTR (MOEX ISS) → валидация → расчёт фазы → marketsaw.json.

Чистый stdlib (как build_data) — гоняется в GitHub Actions, НЕ из браузера пользователя.
Жёсткий гейт (раздел 13 спеки): если расчёт не прошёл валидацию — НЕ публикуем новый файл,
сохраняем предыдущий валидный, пишем ошибку в лог, выходим с кодом 1 (фронт покажет fallback).

CLI:  python market_saw/production/build_marketsaw.py [out_path]   (default: site/marketsaw.json)
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
MS_ROOT = os.path.dirname(HERE)                       # market_saw/
REPO = os.path.dirname(MS_ROOT)
sys.path.insert(0, os.path.join(MS_ROOT, "shared"))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "scripts"))
from mcftr_fetch import discover_board, fetch_mcftr_history  # noqa: E402
import market_saw as ms  # noqa: E402
try:
    import trading_calendar as tc  # торговый календарь MOEX (выходной ≠ stale)
except Exception:  # noqa: BLE001
    tc = None

DEFAULT_OUT = os.path.join(REPO, "site", "marketsaw.json")
MIN_POINTS = 100
STALE_WARN_DAYS = 5          # старше 5 кал. дней → предупреждение в UI
STALE_FAIL_DAYS = 20         # старше 20 кал. дней → битый пайплайн (новогодние каникулы РФ ~10 дн — норма)
SCHEMA = 1


def log(msg: str) -> None:
    sys.stderr.write(f"[build_marketsaw] {msg}\n")


def load_and_clean() -> list:
    """Скачать MCFTR, отбросить null/неположит. close, дедуп дат (последняя), сортировка по возрастанию."""
    eng, mkt, brd = discover_board()
    log(f"board: engine={eng} market={mkt} board={brd}")
    if brd.upper() in ("IMOEX", "MOEXBC"):           # запрет price-only индекса как основы
        raise RuntimeError(f"недопустимая доска {brd}: нужен MCFTR (total return)")
    hist = fetch_mcftr_history(eng, mkt, brd)        # [(date, close)], отсортировано, close>0
    seen = {}
    for d, c in hist:
        if d and c is not None and c > 0:
            seen[d] = float(c)                        # дедуп: последняя цена за дату
    points = [{"date": d, "close": seen[d]} for d in sorted(seen)]
    return points


def validate(points: list, saw: dict) -> None:
    """Гейт публикации. Любое нарушение → RuntimeError (не публикуем)."""
    if len(points) < MIN_POINTS:
        raise RuntimeError(f"points={len(points)} < {MIN_POINTS}")
    dts = [p["date"] for p in points]
    if dts != sorted(dts):
        raise RuntimeError("даты не отсортированы по возрастанию")
    if len(set(dts)) != len(dts):
        raise RuntimeError("дубликаты дат после чистки")
    if any(p["close"] is None or not (p["close"] > 0) for p in points):
        raise RuntimeError("есть null/неположительный close")
    cp = saw.get("current_phase")
    if not cp:
        raise RuntimeError("нет current_phase")
    if abs(cp["current_price"] - round(points[-1]["close"], 2)) > 1e-6:
        raise RuntimeError("current_price ≠ последней валидной точке")
    if cp["direction"] not in ("up", "down"):
        raise RuntimeError(f"неизвестное направление {cp['direction']}")
    # anchor совпадает с подтверждённым экстремумом нужного типа
    want_type = "low" if cp["direction"] == "up" else "high"
    anchors = [e for e in saw["extremes"] if e["type"] == want_type and e["date"] == cp["anchor_date"]]
    if not anchors and saw["extremes"]:
        raise RuntimeError("anchor_date не совпадает с подтверждённым экстремумом")
    p = cp["historical_reach_probability"]
    if p is not None and not (0.0 <= p <= 1.0):
        raise RuntimeError(f"reach_probability вне [0,1]: {p}")


def waves_stats(waves: list) -> dict:
    out = {}
    for d in ("up", "down"):
        amps = sorted(abs(w["amplitude_pct"]) for w in waves if w["direction"] == d)
        durs = sorted(w["duration_days"] for w in waves if w["direction"] == d)
        if amps:
            mid = len(amps) // 2
            med_a = amps[mid] if len(amps) % 2 else (amps[mid - 1] + amps[mid]) / 2
            med_d = durs[len(durs) // 2]
            out[d] = {"count": len(amps), "median_amp": round(med_a, 4),
                      "max_amp": round(amps[-1], 4), "median_days": med_d}
        else:
            out[d] = {"count": 0, "median_amp": None, "max_amp": None, "median_days": None}
    return out


def build(points: list) -> dict:
    saw = ms.calculate_market_saw(points)
    validate(points, saw)
    data_last = points[-1]["date"]
    stale_days = (date.today() - date.fromisoformat(data_last)).days
    if stale_days > STALE_FAIL_DAYS:
        raise RuntimeError(f"последняя дата {data_last} старше {STALE_FAIL_DAYS} дн — пайплайн похоже сломан")
    # Флаг stale — по торговому календарю MOEX: предупреждаем только если ≥2 торговых сессий
    # не отражены (выходной/праздник сам по себе не делает данные устаревшими). Фолбэк на
    # календарный порог, если календарь недоступен.
    if tc is not None:
        now_msk = tc.to_msk(datetime.now(timezone.utc))
        sessions_behind = tc.sessions_between(date.fromisoformat(data_last), now_msk)
        stale_flag = sessions_behind >= 2
    else:
        sessions_behind = None
        stale_flag = stale_days > STALE_WARN_DAYS
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_last": data_last,
        "stale": stale_flag,
        "stale_days": stale_days,
        "sessions_behind": sessions_behind,
        "index": "MCFTR",
        "reversal_threshold": ms.REVERSAL_THRESHOLD,
        "zones": {
            "up": [{"thr": t, "label": l, "risk": r} for t, l, r in ms.UP_ZONES],
            "down": [{"thr": t, "label": l, "risk": r} for t, l, r in ms.DOWN_ZONES],
        },
        "current_phase": saw["current_phase"],
        "extremes": saw["extremes"],
        "waves": saw["waves"],
        "waves_stats": waves_stats(saw["waves"]),
        "series": [[p["date"], round(p["close"], 2)] for p in points],
    }


def main() -> int:
    out_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT
    try:
        points = load_and_clean()
        payload = build(points)
    except Exception as e:  # noqa: BLE001
        log(f"СТОП, не публикуем: {e}")
        if os.path.exists(out_path):
            log(f"сохранён предыдущий валидный {out_path}")
        return 1
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, out_path)                          # атомарная замена — не оставляем полупустой файл
    cp = payload["current_phase"]
    log(f"OK → {out_path}  ({len(payload['series'])} точек, {len(payload['waves'])} волн)")
    log(f"фаза: {cp['direction']} «{cp['label']}» move={ms.format_pct(cp['move_pct'])} "
        f"reach={cp['historical_reach_probability']}  data_last={payload['data_last']} stale={payload['stale']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
