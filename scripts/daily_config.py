#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Daily Market Data Foundation / Risk Engine — единый источник истины по константам.

Пороги НЕ дублировать в других местах: импортировать отсюда. Изменение любого порога —
L3-решение, покрывается тестами.
"""
from __future__ import annotations

# ── annualization ──
TRADING_DAYS_YEAR = 252          # число торговых сессий в году; √N для annualized vol. НЕ 365.
DDOF = 1                         # выборочное стандартное отклонение (n-1)

# ── корпоративные действия ──
EXTREME_DAILY_RETURN = 0.5       # |дневная простая доходность| выше → suspected corporate action
SPLIT_SANE_MAX = 1000.0          # |сплит-фактор| больше → аномалия, блокировать пайплайн
SPLIT_MIN_DETECT = 1.5           # разрыв цены в разы ниже — не считаем сплит-подобным

# ── пороги истории (ОБЩИЕ торговые дни пересечения) — единый источник ──
HISTORY_UNAVAILABLE = 60         # < 60 → risk engine unavailable
HISTORY_INSUFFICIENT = 125       # 60..124 → insufficient_history
HISTORY_USABLE = 252             # 125..251 → usable_with_warning
HISTORY_HIGH_CONF = 756          # >= 756 → high confidence для хвостовых метрик

# ── risk engine ──
VAR_LEVELS = (0.95, 0.99)        # уровни доверия VaR/CVaR
CVAR_MIN_TAIL = 5                # < наблюдений в хвосте → CVaR low confidence
RISK_CONTRIB_TOL = 1e-6          # допуск сходимости Σ component contributions ≈ σ_p
BENCHMARK = "IMOEX"              # бенчмарк beta/correlation
