#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""IMOEX — ценовой контур «Пилы». Публичная воспроизводимая реализация swing/zigzag-подхода
по ЦЕНОВОМУ индексу IMOEX (не total return). Использует общий zigzag_engine с порогом 7% и
отдельно считает уровни покупки/фиксации и текущую зону. Чистый stdlib.

НЕ бренд и не точная копия закрытой методики — публичная воспроизводимая реализация.
Зоны buy/neutral/fix НЕ смешиваются с зонами MCFTR (investment_regime).
"""
from __future__ import annotations

import os
import sys
from typing import List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "shared"))
sys.path.insert(0, HERE)
import zigzag_engine as zz  # noqa: E402
from config import get_config  # noqa: E402

ZONE_LABEL = {"buy": "Зона покупки", "neutral": "Нейтральная зона", "fix": "Зона фиксации"}


def classify_zone(price: float, buy_level: Optional[float], fix_level: Optional[float]) -> str:
    """buy при цене ≤ уровня покупки; fix при цене ≥ уровня фиксации; иначе neutral.

    ВАЖНО: уровни МОГУТ пересекаться. buy=high·0.87, fix=low·1.17. Когда диапазон широкий
    (high/low > ~1.345), buy_level > fix_level и зоны перекрываются в [fix_level, buy_level]:
    цена там одновременно «≥13% ниже максимума» И «≥17% выше минимума» (рынок пилило). Порядок
    проверки buy→fix детерминированно разрешает перекрытие в пользу buy (консервативно для
    докупки на просадке). Neutral существует ТОЛЬКО когда buy_level < fix_level (узкий диапазон)."""
    if buy_level is not None and price <= buy_level:
        return "buy"
    if fix_level is not None and price >= fix_level:
        return "fix"
    return "neutral"


def _explanation(zone: str, move_from_high: float, move_from_low: float) -> str:
    if zone == "buy":
        return (f"IMOEX опустился на {move_from_high * 100:+.1f}% от последнего подтверждённого максимума и "
                "вошёл в ценовую зону покупки. Это не сигнал «покупать всё», а повод постепенно докупать "
                "качественные бумаги. Ценовой индекс не учитывает дивиденды — для полной картины смотрите MCFTR.")
    if zone == "fix":
        return (f"IMOEX вырос на {move_from_low * 100:+.1f}% от последнего подтверждённого минимума и вошёл в "
                "ценовую зону фиксации. Разумно не наращивать риск агрессивно и проверить долю акций. "
                "Ценовой индекс не учитывает дивиденды — для полной картины смотрите MCFTR.")
    return ("IMOEX между ценовыми уровнями покупки и фиксации — нейтральная зона. Ценовой индекс "
            "не учитывает дивиденды; основной инвестиционный ориентир — MCFTR (полная доходность).")


def compute_imoex(points: List[dict], live_price: Optional[float] = None) -> dict:
    """Построить IMOEX-контур. points — закрытые дневные CLOSE (сортировка возр.).
    live_price — необязательная внутридневная котировка ТОЛЬКО для текущей позиции (не подтверждает
    экстремумы). Возвращает поля контракта IMOEX payload (без meta/data_quality — их добавляет билдер)."""
    cfg = get_config("IMOEX")
    z = zz.run_zigzag(points, cfg["reversal_threshold"])
    high = zz.last_confirmed(z["extremes"], "high")
    low = zz.last_confirmed(z["extremes"], "low")

    buy_level = round(high["price"] * (1 - cfg["buy_drawdown"]), 2) if high else None
    fix_level = round(low["price"] * (1 + cfg["fix_rally"]), 2) if low else None

    official_price = points[-1]["close"]
    # live_price используется ТОЛЬКО как текущая позиция; при отсутствии — официальный CLOSE
    current_price = float(live_price) if (live_price is not None and live_price > 0) else official_price
    is_live = live_price is not None and live_price > 0 and abs(current_price - official_price) > 1e-9

    zone = classify_zone(current_price, buy_level, fix_level)
    move_from_high = (current_price / high["price"] - 1.0) if high else 0.0   # <0 при просадке
    move_from_low = (current_price / low["price"] - 1.0) if low else 0.0      # >0 при росте
    dist_to_buy = (current_price / buy_level - 1.0) if buy_level else None    # >0: выше уровня покупки
    dist_to_fix = (current_price / fix_level - 1.0) if fix_level else None    # <0: ниже уровня фиксации

    return {
        "index": "IMOEX", "index_type": "price", "methodology": "price_saw",
        "reversal_threshold": cfg["reversal_threshold"],
        "buy_drawdown": cfg["buy_drawdown"], "fix_rally": cfg["fix_rally"],
        "last_confirmed_high": ({"date": high["date"], "price": high["price"]} if high else None),
        "last_confirmed_low": ({"date": low["date"], "price": low["price"]} if low else None),
        "levels": {"buy": buy_level, "fix": fix_level},
        "current_state": {
            "price": round(current_price, 2),
            "price_type": "live" if is_live else "official",
            "zone": zone, "zone_label": ZONE_LABEL[zone],
            "move_from_high_pct": round(move_from_high, 5),
            "move_from_low_pct": round(move_from_low, 5),
            "distance_to_buy_pct": (round(dist_to_buy, 5) if dist_to_buy is not None else None),
            "distance_to_fix_pct": (round(dist_to_fix, 5) if dist_to_fix is not None else None),
            "explanation": _explanation(zone, move_from_high, move_from_low),
        },
        "extremes": z["extremes"], "waves": z["waves"],
        "waves_stats": zz.waves_stats(z["waves"]),
        "series": [[p["date"], round(p["close"], 2)] for p in points],
    }
