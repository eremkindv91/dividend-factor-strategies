#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Daily Market Data Foundation — Фаза 3: централизованные формулы доходностей.

Единственное место расчёта. НЕ смешивать simple/log/price/total — downstream явно выбирает
тип. NaN на пропуске (не 0-доходность). Округление — только на UI-слое, здесь полные float.
"""
from __future__ import annotations

import math


def _valid(x):
    return isinstance(x, (int, float)) and x is not None and not (isinstance(x, float) and math.isnan(x)) and x > 0


def simple_returns(closes: list) -> list:
    """r_t = c_t / c_{t-1} − 1. None где предыдущая/текущая цена невалидна (не 0-доходность)."""
    out = []
    for i in range(1, len(closes)):
        p, c = closes[i - 1], closes[i]
        out.append((c / p - 1.0) if _valid(p) and _valid(c) else None)
    return out


def log_returns(closes: list) -> list:
    """r_t = ln(c_t / c_{t-1}). None где цена невалидна."""
    out = []
    for i in range(1, len(closes)):
        p, c = closes[i - 1], closes[i]
        out.append(math.log(c / p) if _valid(p) and _valid(c) else None)
    return out


def total_returns(adj_closes: list, dividends: list | None = None) -> list:
    """TR_t = (adj_c_t + div_t) / adj_c_{t-1} − 1. dividends — денежный дивиденд на дату t
    (0/None где нет). Требует УЖЕ скорректированных на сплит цен (adjusted_close)."""
    n = len(adj_closes)
    divs = dividends or [0.0] * n
    out = []
    for i in range(1, n):
        p, c = adj_closes[i - 1], adj_closes[i]
        d = divs[i] or 0.0
        out.append(((c + d) / p - 1.0) if _valid(p) and _valid(c) else None)
    return out


def drop_none(returns: list) -> list:
    """Убрать None (для метрик по имеющимся наблюдениям); порядок сохранён."""
    return [r for r in returns if r is not None]
