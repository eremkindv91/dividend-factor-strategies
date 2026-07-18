#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Общий zigzag state machine для обоих контуров «Пилы» (MCFTR и IMOEX).

Извлечён из production/market_saw.py БЕЗ изменения алгоритма — тест
market_saw/tests/test_zigzag_engine.py доказывает бит-идентичность extremes/waves с
канонической MCFTR-реализацией на реальных данных (production-путь MCFTR не переписан,
только подтверждён как эквивалентный общему движку).

Анти-look-ahead: локальный максимум подтверждается ЛИШЬ после падения от него на ≥reversal,
минимум — лишь после отскока на ≥reversal. Текущая (незавершённая) волна НЕ попадает в waves.
Расчёт воспроизводим при последовательном добавлении точек по одному торговому дню. Чистый stdlib.
"""
from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional


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


def run_zigzag(points: List[dict], reversal: float) -> dict:
    """Построить подтверждённые экстремумы + завершённые волны по ряду цен.

    points — отсортированный по возрастанию список {date:'YYYY-MM-DD', close:float>0}.
    reversal — порог подтверждения смены волны (доля, напр. 0.095 или 0.07).
    Возвращает {extremes, waves, direction, pivot, last, cand_high, cand_low} — сырое состояние
    zigzag, на котором контуры строят свою бизнес-логику (фазу/зоны/уровни). Бросает ValueError
    на битом/пустом вводе.
    """
    if not points:
        raise ValueError("run_zigzag: пустой ряд")
    for p in points:
        if p.get("close") is None or not (p["close"] > 0):
            raise ValueError(f"run_zigzag: невалидный close на {p.get('date')}")

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
            if c / cand_high["close"] - 1.0 <= -reversal:   # падение ≥reversal от максимума → максимум подтверждён
                extremes.append({"date": cand_high["date"], "price": round(cand_high["close"], 2), "type": "high"})
                waves.append(_wave("up", pivot, cand_high))
                direction = "down"; pivot = cand_high; cand_low = p
        else:  # direction == "down"
            if c < cand_low["close"]:
                cand_low = p
            if c / cand_low["close"] - 1.0 >= reversal:     # отскок ≥reversal от минимума → минимум подтверждён
                extremes.append({"date": cand_low["date"], "price": round(cand_low["close"], 2), "type": "low"})
                waves.append(_wave("down", pivot, cand_low))
                direction = "up"; pivot = cand_low; cand_high = p

    return {
        "extremes": extremes, "waves": waves,
        "direction": direction, "pivot": pivot, "last": points[-1],
        "cand_high": cand_high, "cand_low": cand_low,
    }


def waves_stats(waves: List[dict]) -> dict:
    """Медианы/макс амплитуд и длительностей по направлениям — survival-статистика прошлого."""
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


def last_confirmed(extremes: List[dict], kind: str) -> Optional[dict]:
    """Последний подтверждённый экстремум типа 'high'/'low' (или None)."""
    for e in reversed(extremes):
        if e["type"] == kind:
            return e
    return None
