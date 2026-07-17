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

# ── v2: EWMA VaR ──
EWMA_LAMBDA = 0.94                 # RiskMetrics-стандарт; вес затухания старых наблюдений

# ── v2: Monte Carlo — SEEDED NON-PARAMETRIC bootstrap (не параметрическая нормальная модель) ──
MC_SEED = 42
MC_SIMS_POINT = 20000              # точечная оценка (разовый расчёт на портфель)
MC_SIMS_BACKTEST = 300             # уменьшено для практической скорости rolling-бэктеста
                                    # (сотни окон × симуляции); точность квантиля на уровне окна
                                    # частично усредняется по многим прогонам бэктеста
MC_AUTOCORR_BLOCK_THRESHOLD = 0.10  # |lag-1 автокорреляция КВАДРАТОВ доходности| (ARCH/кластеризация
                                    # волатильности) ≥ порога → block bootstrap вместо IID
MC_BLOCK_LENGTH = 5                 # длина блока (торговая неделя) при обнаруженной кластеризации

# ── v2: Cornish-Fisher — explicit gate (никогда не клампить молча) ──
CF_MIN_OBS = HISTORY_USABLE         # < этого → "недостаточная история" (нужна устойчивая оценка 3-4 момента)
CF_MAX_ABS_EXCESS_KURTOSIS = 15.0   # |excess kurtosis| выше → "неустойчивый excess kurtosis"

# ── v2: VaR backtest (rolling out-of-sample, ROLLING policy — не expanding) ──
BACKTEST_WINDOW = TRADING_DAYS_YEAR  # начальное окно оценки (252 торговых дня)
BACKTEST_HORIZON_DAYS = 1            # горизонт прогноза VaR
MIN_BACKTEST_FORECASTS = 100         # < прогнозов вне выборки → insufficient_backtest_history
KUPIEC_REJECT_LR = 3.841             # LR > это (chi2, df=1, 95%) → отклонить калибровку
