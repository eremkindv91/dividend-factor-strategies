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
CVAR_MIN_TAIL = 5                # < наблюдений в хвосте → CVaR low confidence / не показывать
RISK_CONTRIB_TOL = 1e-6          # допуск сходимости Σ component contributions ≈ σ_p
BENCHMARK = "IMOEX"              # бенчмарк beta/correlation
QUANTILE_METHOD = "linear_interpolation"   # тип-7 (как numpy default); детерминирован
MIN_COMMON_OBS = HISTORY_UNAVAILABLE       # < общих торговых дней → метрики недоступны
VALUE_COVERAGE_PARTIAL = 0.85    # покрытие стоимости ниже → метрики partial, confidence ≤ medium
VALUE_COVERAGE_MIN = 0.50        # ниже → confidence low, общий риск помечается partial
FLOAT_TOL = 1e-9                 # допуск инвариантов
MAR_DAILY = 0.0                  # minimum acceptable return для downside deviation (дневной, 0%)
