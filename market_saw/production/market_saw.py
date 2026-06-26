#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase Engine «Помощника фазы рынка» (swing/zigzag market phase model).

Чистый stdlib — крутится и оффлайн, и в CI live-пути (генерация marketsaw.json). НИКАКИХ
pandas/numpy/sklearn и НИКАКОГО z-score/SMA/RSI: фаза определяется ТОЛЬКО через zigzag-логику по
индексу полной доходности MCFTR — локальные high/low с порогом разворота 9.5% → текущая волна → фаза.

Анти-leakage: локальный максимум подтверждается лишь ПОСЛЕ падения от него на ≥9.5%, минимум —
лишь после отскока на ≥9.5%. Текущая (незавершённая) волна НЕ попадает в массив завершённых waves.

Модель — воспроизводимая публичная версия swing/zigzag-подхода к фазам рынка. Не бренд и не точная
копия какой-либо закрытой методики. Это индикатор фазы, НЕ торговый сигнал и НЕ прогноз.
"""
from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

# Базовые параметры — ФИКСИРОВАНЫ (без задней подгонки). Это дефолты конфигурации.
REVERSAL_THRESHOLD = 0.095          # порог подтверждения смены волны (±9.5%)
DOWN_ZONES = [                      # по |просадке| от подтверждённого максимума, по возрастанию
    (0.00,  "Начальная коррекция",                         "neutral"),
    (0.095, "Коррекция подтверждена",                      "watch"),
    (0.13,  "Зона осторожной докупки",                     "buy_zone"),
    (0.18,  "Глубокая просадка / зона повышенного интереса", "strong_buy_zone"),
]
UP_ZONES = [                        # по ралли от подтверждённого минимума, по возрастанию
    (0.00,  "Ранний отскок",                               "neutral"),
    (0.095, "Рост подтверждён",                            "positive"),
    (0.17,  "Зона частичного снижения риска",              "reduce_zone"),
    (0.22,  "Рынок сильно вырос / повышенный риск перегрева", "strong_reduce_zone"),
]


def format_pct(value: float) -> str:
    """0.1712 → '+17.1%';  -0.132 → '-13.2%'. Знак явный, один знак после запятой."""
    return f"{value * 100:+.1f}%"


def classify_market_phase(direction: str, move_pct: float) -> Dict[str, str]:
    """Движение от последнего экстремума → человекочитаемая фаза + risk_level."""
    zones = UP_ZONES if direction == "up" else DOWN_ZONES
    x = abs(move_pct)                              # просадка хранится отрицательной — берём модуль
    label, risk = zones[0][1], zones[0][2]
    for thr, lab, rk in zones:
        if x >= thr:
            label, risk = lab, rk
    return {"label": label, "risk_level": risk}


def _days_between(d0: str, d1: str) -> int:
    return (date.fromisoformat(d1) - date.fromisoformat(d0)).days


def _wave(direction: str, start: dict, end: dict) -> dict:
    return {
        "direction": direction,
        "start_date": start["date"], "end_date": end["date"],
        "start_price": round(start["close"], 2), "end_price": round(end["close"], 2),
        "amplitude_pct": round(end["close"] / start["close"] - 1.0, 5),  # знаковая: up>0, down<0
        "duration_days": _days_between(start["date"], end["date"]),
    }


def calculate_historical_reach_probability(
    waves: List[dict], direction: str, current_move_abs_pct: float
) -> Optional[float]:
    """
    Доля ЗАВЕРШЁННЫХ волн того же направления, чья амплитуда ≥ текущей по модулю.
    Survival-статистика прошлого, НЕ прогноз. Нет волн направления → None.
    """
    pool = [w for w in waves if w["direction"] == direction]
    if not pool:
        return None
    reached = sum(1 for w in pool if abs(w["amplitude_pct"]) >= current_move_abs_pct)
    return reached / len(pool)


def _explanation(direction: str, move_pct: float, prob: Optional[float]) -> str:
    mv = format_pct(move_pct)
    if prob is None:
        freq = "По завершённым волнам этого направления пока нет статистики."
    else:
        freq = (f"Исторически движения такой амплитуды или больше встречались в "
                f"{prob * 100:.0f}% завершённых {'ростовых' if direction == 'up' else 'падающих'} волн.")
    if direction == "up":
        return (f"Рынок вырос на {mv} от последнего локального минимума. {freq} "
                "Это не прогноз, а индикатор текущей фазы. В этой зоне разумно не наращивать риск "
                "агрессивно и проверить долю акций в портфеле.")
    return (f"Рынок снизился на {mv} от последнего локального максимума. {freq} "
            "Это не сигнал «покупать всё», а повод подготовить список качественных бумаг и "
            "докупать постепенно.")


def calculate_market_saw(points: List[dict], reversal: float = REVERSAL_THRESHOLD) -> dict:
    """
    Построить zigzag-экстремумы, завершённые волны и текущую фазу по ряду MCFTR.
    points — отсортированный по возрастанию список {date:'YYYY-MM-DD', close:float>0}.
    Возвращает {extremes, waves, current_phase}. Бросает ValueError при битом/коротком вводе.
    """
    if not points:
        raise ValueError("calculate_market_saw: пустой ряд")
    for p in points:
        if p["close"] is None or not (p["close"] > 0):
            raise ValueError(f"calculate_market_saw: невалидный close на {p.get('date')}")

    extremes: List[dict] = []
    waves: List[dict] = []
    direction: Optional[str] = None
    pivot = points[0]                 # последний ПОДТВЕРЖДЁННЫЙ экстремум
    cand_high = points[0]
    cand_low = points[0]

    for p in points[1:]:
        c = p["close"]
        if direction is None:
            if c > cand_high["close"]:
                cand_high = p
            if c < cand_low["close"]:
                cand_low = p
            if c / cand_low["close"] - 1.0 >= reversal:
                direction = "up"; pivot = cand_low
                extremes.append({"date": cand_low["date"], "price": round(cand_low["close"], 2), "type": "low"})
                cand_high = p
            elif c / cand_high["close"] - 1.0 <= -reversal:
                direction = "down"; pivot = cand_high
                extremes.append({"date": cand_high["date"], "price": round(cand_high["close"], 2), "type": "high"})
                cand_low = p
        elif direction == "up":
            if c > cand_high["close"]:
                cand_high = p
            if c / cand_high["close"] - 1.0 <= -reversal:   # падение ≥9.5% от максимума → максимум подтверждён
                extremes.append({"date": cand_high["date"], "price": round(cand_high["close"], 2), "type": "high"})
                waves.append(_wave("up", pivot, cand_high))
                direction = "down"; pivot = cand_high; cand_low = p
        else:  # direction == "down"
            if c < cand_low["close"]:
                cand_low = p
            if c / cand_low["close"] - 1.0 >= reversal:     # отскок ≥9.5% от минимума → минимум подтверждён
                extremes.append({"date": cand_low["date"], "price": round(cand_low["close"], 2), "type": "low"})
                waves.append(_wave("down", pivot, cand_low))
                direction = "up"; pivot = cand_low; cand_high = p

    last = points[-1]
    if direction is None:
        # за всю историю не набралось ни одного движения ±9.5% (для 23 лет MCFTR невозможно)
        anchor, dir_ = pivot, "up"
    else:
        anchor, dir_ = pivot, direction
    move_pct = last["close"] / anchor["close"] - 1.0
    cls = classify_market_phase(dir_, move_pct)
    prob = calculate_historical_reach_probability(waves, dir_, abs(move_pct))

    current_phase = {
        "direction": dir_,
        "label": cls["label"], "risk_level": cls["risk_level"],
        "anchor_date": anchor["date"], "anchor_price": round(anchor["close"], 2),
        "current_date": last["date"], "current_price": round(last["close"], 2),
        "move_pct": round(move_pct, 5),
        "historical_reach_probability": (round(prob, 4) if prob is not None else None),
        "explanation": _explanation(dir_, move_pct, prob),
    }
    return {"extremes": extremes, "waves": waves, "current_phase": current_phase}
