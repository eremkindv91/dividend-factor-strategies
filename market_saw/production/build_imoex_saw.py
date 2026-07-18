#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Production-билдер ценового контура IMOEX: MOEX ISS → валидация → marketsaw_imoex.json.

Отдельный от build_marketsaw.py (MCFTR): контуры не смешиваются, сбой одного не роняет
другой. Чистый stdlib. Официальный CLOSE подтверждает экстремумы; live snapshot — только
текущая позиция маркера (provisional), НЕ подтверждает pivot и НЕ входит в history/waves.

Атомарная запись, last-good при сбое (файл не перезаписывается пустым). Выход 1 при сбое —
тогда предыдущий валидный файл остаётся (workflow сохраняет per-индекс last-good).

CLI:  python market_saw/production/build_imoex_saw.py [out_path]   (default site/marketsaw_imoex.json)
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
MS_ROOT = os.path.dirname(HERE)
REPO = os.path.dirname(MS_ROOT)
sys.path.insert(0, os.path.join(MS_ROOT, "shared"))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "scripts"))
import moex_index_fetch as fetch  # noqa: E402
import imoex_engine as ie  # noqa: E402
from config import get_config, SCHEMA, MIN_POINTS, STALE_FAIL_DAYS  # noqa: E402
try:
    import trading_calendar as tc  # noqa: E402
except Exception:  # noqa: BLE001
    tc = None

DEFAULT_OUT = os.path.join(REPO, "site", "marketsaw_imoex.json")


def log(m: str) -> None:
    sys.stderr.write(f"[build_imoex] {m}\n")


def load_and_clean(secid: str = "IMOEX") -> tuple[list, tuple]:
    """Скачать историю IMOEX (закрытые сессии), дедуп дат, сортировка. Вернуть (points, board)."""
    board = fetch.discover_index_board(secid)
    log(f"board: {board[0]}/{board[1]}/{board[2]}")
    hist = fetch.fetch_index_history(secid, *board)
    seen = {}
    for d, c in hist:
        if d and c is not None and c > 0:
            seen[d] = float(c)
    points = [{"date": d, "close": seen[d]} for d in sorted(seen)]
    return points, board


def validate(points: list, payload: dict) -> None:
    """Гейт публикации IMOEX. Любое нарушение → RuntimeError (не публикуем)."""
    if len(points) < MIN_POINTS:
        raise RuntimeError(f"points={len(points)} < {MIN_POINTS}")
    dts = [p["date"] for p in points]
    if dts != sorted(dts):
        raise RuntimeError("даты не отсортированы")
    if len(set(dts)) != len(dts):
        raise RuntimeError("дубликаты дат")
    if any(p["close"] is None or not (p["close"] > 0) for p in points):
        raise RuntimeError("null/неположительный close")
    if payload["index"] != "IMOEX":
        raise RuntimeError("index != IMOEX")
    if "current_phase" in payload or "reversal_threshold" not in payload:
        raise RuntimeError("контракт IMOEX повреждён (методика MCFTR?)")
    if payload["current_state"]["zone"] not in ("buy", "neutral", "fix"):
        raise RuntimeError(f"неизвестная зона {payload['current_state']['zone']}")
    hi, lo = payload["last_confirmed_high"], payload["last_confirmed_low"]
    lv = payload["levels"]
    if hi and lv["buy"] is not None and not (lv["buy"] < hi["price"]):
        raise RuntimeError("buy_level должен быть < last_confirmed_high")
    if lo and lv["fix"] is not None and not (lv["fix"] > lo["price"]):
        raise RuntimeError("fix_level должен быть > last_confirmed_low")
    # confirmed extremes принадлежат ряду; high/low чередуются
    prices = {p["date"]: round(p["close"], 2) for p in points}
    types = []
    for e in payload["extremes"]:
        if prices.get(e["date"]) != e["price"]:
            raise RuntimeError(f"экстремум {e['date']} не принадлежит ряду")
        types.append(e["type"])
    if any(types[i] == types[i + 1] for i in range(len(types) - 1)):
        raise RuntimeError("high/low не чередуются")


def build(points: list, board: tuple, snapshot: dict | None) -> dict:
    live_price = snapshot["price"] if snapshot else None
    payload = ie.compute_imoex(points, live_price=live_price)

    data_last = points[-1]["date"]
    stale_days = (date.today() - date.fromisoformat(data_last)).days
    if stale_days > STALE_FAIL_DAYS:
        raise RuntimeError(f"последняя дата {data_last} старше {STALE_FAIL_DAYS} дн")
    if tc is not None:
        now_msk = tc.to_msk(datetime.now(timezone.utc))
        sessions_behind = tc.sessions_between(date.fromisoformat(data_last), now_msk)
    else:
        sessions_behind = None
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # official_close — последняя завершённая сессия; live_snapshot — внутридневной (provisional)
    official_close = {"date": data_last, "price": round(points[-1]["close"], 2)}
    live_snapshot = None
    if snapshot:
        live_snapshot = {
            "available": True, "timestamp": now_iso, "price": round(snapshot["price"], 2),
            "provisional": bool(snapshot.get("is_intraday")),
            "systime": snapshot.get("systime"), "source": "MOEX ISS marketdata",
        }

    payload.update({
        "schema": SCHEMA,
        "generated_at": now_iso,
        "data_last": data_last,
        "sessions_behind": sessions_behind,
        "stale_days": stale_days,
        "official_close": official_close,
        "live_snapshot": live_snapshot,
        "data_quality": {
            "status": "good", "board": board[2], "observations": len(points),
            "first_date": points[0]["date"], "last_date": data_last,
            "sessions_behind": sessions_behind,
        },
        "fallback": False, "fallback_reason": None, "last_success_at": now_iso,
    })
    return payload


def main() -> int:
    out_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT
    try:
        points, board = load_and_clean("IMOEX")
        snapshot = fetch.fetch_index_snapshot("IMOEX", *board)   # None при сбое — не критично
        payload = build(points, board, snapshot)
        validate(points, payload)
    except Exception as e:  # noqa: BLE001
        log(f"СТОП, не публикуем: {e}")
        if os.path.exists(out_path):
            log(f"сохранён предыдущий валидный {out_path}")
        return 1
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, out_path)
    cs = payload["current_state"]
    log(f"OK → {out_path}  ({len(payload['series'])} точек, {len(payload['waves'])} волн)")
    log(f"зона: {cs['zone']} «{cs['zone_label']}» цена={cs['price']} ({cs['price_type']}) "
        f"buy={payload['levels']['buy']} fix={payload['levels']['fix']} data_last={payload['data_last']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
