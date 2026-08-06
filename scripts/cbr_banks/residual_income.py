#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Residual Income Model для банков.

Почему именно RI, а не DCF: у банка долг — это сырьё бизнеса, а не источник
финансирования, поэтому свободный денежный поток фирмы для него не определён.
RI работает от капитала и рентабельности, которые у банка наблюдаемы.

    RI_t = NI_t − k_e × BV_(t−1) = (ROE_t − k_e) × BV_(t−1)

    V_0 = BV_0 + Σ RI_t / Π(1+k_e,j) + TV_T / Π(1+k_e,j)

Смысл: банк стоит своего капитала ПЛЮС приведённая стоимость сверхдоходности.
Если ROE = k_e, то RI = 0 и справедливый P/BV = 1 — это и есть проверка №1.

ТОЛЬКО stdlib: workflow update-cbr-banks.yml не делает pip install
(см. banks_config.json → _note). Никаких numpy/scipy здесь быть не должно.

Модуль НЕ решает, можно ли публиковать результат — это дело гейта качества.
Он лишь честно отказывается считать то, что не считается: вместо NaN/Infinity
возвращается None с кодом причины.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


# Порог, ниже которого разница (k_e − g) считается вырожденной: терминальная
# стоимость взрывается, и любое число на выходе будет фикцией точности.
MIN_TERMINAL_SPREAD = 0.005          # 0.5 п.п.

# Ниже этого различия ROE и k_e считаются равными: остаточный доход — ноль.
ROE_SPREAD_EPS = 1e-12


def _finite(x) -> bool:
    """Ни одно нефинитное число не должно покинуть модуль (§31: без NaN/Infinity)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return False
    return math.isfinite(v)


@dataclass
class ForecastYear:
    """Один прогнозный год. Все суммы — в рублях, ставки — в долях единицы."""
    year: int
    opening_equity: float
    roe: float
    net_income: float
    payout: float
    dividends: float
    other_equity_change: float
    closing_equity: float
    cost_of_equity: float
    residual_income: float
    discount_factor: float
    present_value: float

    def as_dict(self) -> dict:
        return {
            "year": self.year,
            "opening_equity": round(self.opening_equity, 2),
            "roe": round(self.roe, 6),
            "net_income": round(self.net_income, 2),
            "payout": round(self.payout, 6),
            "dividends": round(self.dividends, 2),
            "other_equity_change": round(self.other_equity_change, 2),
            "closing_equity": round(self.closing_equity, 2),
            "cost_of_equity": round(self.cost_of_equity, 6),
            "residual_income": round(self.residual_income, 2),
            "discount_factor": round(self.discount_factor, 8),
            "present_value": round(self.present_value, 2),
        }


@dataclass
class Valuation:
    """Итог оценки. `ok=False` означает «посчитать нельзя», а не «стоит ноль»."""
    ok: bool
    reason: str | None = None
    equity_value: float | None = None
    fair_pbv: float | None = None
    fair_price_per_share: float | None = None
    pv_explicit: float | None = None
    pv_terminal: float | None = None
    terminal_share: float | None = None
    years: list[ForecastYear] = field(default_factory=list)

    def as_dict(self) -> dict:
        if not self.ok:
            return {"ok": False, "reason": self.reason}
        return {
            "ok": True,
            "equity_value": round(self.equity_value, 2),
            "fair_pbv": round(self.fair_pbv, 4),
            "fair_price_per_share": (round(self.fair_price_per_share, 2)
                                     if self.fair_price_per_share is not None else None),
            "decomposition": {
                "book_value": round(self.equity_value - self.pv_explicit - self.pv_terminal, 2),
                "pv_explicit_ri": round(self.pv_explicit, 2),
                "pv_terminal_ri": round(self.pv_terminal, 2),
                # Вклад терминала считается К КАПИТАЛУ, а не к итоговой стоимости: при
                # разрушении стоимости слагаемые разного знака, и доля «от итога» даёт
                # бессмысленные −234%. К капиталу метрика читается всегда: «терминал
                # добавляет/отнимает столько-то процентов балансовой стоимости».
                "terminal_pv_over_book": round(self.terminal_share, 4),
            },
            "years": [y.as_dict() for y in self.years],
        }


def residual_income(net_income: float, opening_equity: float, cost_of_equity: float) -> float:
    """RI = NI − k_e × BV_(t−1). Отрицательный RI — нормальный результат, не ошибка."""
    return float(net_income) - float(cost_of_equity) * float(opening_equity)


def terminal_value(closing_equity: float, roe_terminal: float,
                   cost_of_equity_terminal: float, growth_terminal: float) -> tuple[float | None, str | None]:
    """TV_T = (ROE_term − k_e_term) × BV_T / (k_e_term − g_term).

    При g ≥ k_e формула не имеет экономического смысла (бесконечная стоимость).
    Спека (§6.2) прямо запрещает «скрытые ограничения, которые просто обрезают
    слишком высокую оценку»: поэтому здесь не clamp, а отказ считать.
    """
    for name, v in (("BV_T", closing_equity), ("ROE_term", roe_terminal),
                    ("k_e_term", cost_of_equity_terminal), ("g_term", growth_terminal)):
        if not _finite(v):
            return None, f"terminal_input_not_finite:{name}"

    if float(closing_equity) <= 0:
        return None, "terminal_equity_not_positive"

    # ROE = k_e → остаточный доход тождественно нулевой, и терминал равен нулю при
    # ЛЮБОМ спреде. Это не обрезка «слишком высокой оценки», а предел: банк, который
    # зарабатывает ровно требуемую доходность, стоит своего капитала. Проверять это
    # надо ДО спреда, иначе честный случай payout=0 (тогда g = ROE = k_e) был бы
    # отвергнут как вырожденный.
    excess_roe = float(roe_terminal) - float(cost_of_equity_terminal)
    if abs(excess_roe) < ROE_SPREAD_EPS:
        return 0.0, None

    spread = float(cost_of_equity_terminal) - float(growth_terminal)
    if spread <= 0:
        return None, "terminal_growth_ge_cost_of_equity"
    if spread < MIN_TERMINAL_SPREAD:
        # Формально считается, но результат управляется третьим знаком предпосылки.
        return None, "terminal_spread_degenerate"

    tv = (excess_roe * float(closing_equity)) / spread
    return (tv, None) if _finite(tv) else (None, "terminal_value_not_finite")


def value_equity(opening_book_value: float,
                 years: list[ForecastYear],
                 roe_terminal: float,
                 cost_of_equity_terminal: float,
                 growth_terminal: float,
                 diluted_shares: float | None = None) -> Valuation:
    """Собрать V_0 из капитала, явного периода и терминала.

    Дисконтирование — кумулятивным произведением (1+k_e,j), а не (1+k_e)^t: ставка
    по годам может различаться, и степень молча предположила бы её постоянство.
    """
    if not _finite(opening_book_value):
        return Valuation(False, "book_value_not_finite")
    if float(opening_book_value) <= 0:
        # Отрицательный капитал ломает саму базу модели (§14.3).
        return Valuation(False, "book_value_not_positive")
    if not years:
        return Valuation(False, "empty_forecast")

    pv_explicit = 0.0
    for y in years:
        if not _finite(y.present_value):
            return Valuation(False, f"pv_not_finite:{y.year}")
        pv_explicit += y.present_value

    last = years[-1]
    tv, reason = terminal_value(last.closing_equity, roe_terminal,
                                cost_of_equity_terminal, growth_terminal)
    if tv is None:
        return Valuation(False, reason)

    pv_terminal = tv * last.discount_factor
    equity_value = float(opening_book_value) + pv_explicit + pv_terminal
    if not _finite(equity_value):
        return Valuation(False, "equity_value_not_finite")

    fair_pbv = equity_value / float(opening_book_value)
    price = None
    if diluted_shares is not None:
        if not _finite(diluted_shares) or float(diluted_shares) <= 0:
            return Valuation(False, "diluted_shares_not_positive")
        price = equity_value / float(diluted_shares)

    # Вклад терминала — не украшение: если он сопоставим с самим капиталом, оценка
    # держится на предпосылке об устойчивом ROE, а не на прогнозном периоде.
    terminal_share = pv_terminal / float(opening_book_value)

    return Valuation(True, None, equity_value, fair_pbv, price,
                     pv_explicit, pv_terminal, terminal_share, years)


def justified_pbv_single_stage(roe: float, growth: float, cost_of_equity: float) -> float | None:
    """Одностадийный ОРИЕНТИР P/BV = (ROE − g) / (k_e − g).

    Только диагностика (§6.5). Не справедливая стоимость: игнорирует переходную
    динамику ROE, изменение капитала и payout. В интерфейсе называть
    «Одностадийный ориентир P/BV», не «Справедливая стоимость».
    """
    if not all(_finite(v) for v in (roe, growth, cost_of_equity)):
        return None
    spread = float(cost_of_equity) - float(growth)
    if spread < MIN_TERMINAL_SPREAD:
        return None
    value = (float(roe) - float(growth)) / spread
    return value if _finite(value) else None
