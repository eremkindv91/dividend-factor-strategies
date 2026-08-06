#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Стоимость собственного капитала банка.

    k_e = R_f + β_adj × ERP + LP + ISP

Единый COE 20% для всех банков (как было в ползунке) означал бы, что ВТБ и
Банк Санкт-Петербург несут одинаковый риск. Спека §8 это прямо запрещает, и
это правильно: ползунок остаётся, но как сценарный СДВИГ ко всем банкам сразу,
а не как источник базового значения.

Бета считается вручную по ковариации — в workflow банков нет pip install, а
значит нет ни numpy, ни statsmodels (см. banks_config.json → _note).

Все компоненты хранятся раздельно (§8.1): иначе нельзя показать пользователю,
из чего собралась ставка, и нельзя проверить её глазами.
"""
from __future__ import annotations

import math
from statistics import median


def _clean_pairs(asset: list, benchmark: list) -> list[tuple[float, float]]:
    """Пары наблюдений, где есть ОБА значения.

    Выбрасывать надо именно пары, а не каждый ряд по отдельности: иначе
    доходности разъедутся по времени и бета будет посчитана по разным месяцам.
    """
    out = []
    for a, b in zip(asset, benchmark):
        if a is None or b is None:
            continue
        try:
            fa, fb = float(a), float(b)
        except (TypeError, ValueError):
            continue
        if math.isfinite(fa) and math.isfinite(fb):
            out.append((fa, fb))
    return out


def winsorize(values: list[float], limit: float) -> list[float]:
    """Обрезать хвосты по перцентилю.

    Один месяц вроде февраля 2022 иначе определяет бету целиком: это не
    систематический риск банка, а разовое событие рынка.
    """
    if not values or limit <= 0:
        return list(values)
    s = sorted(values)
    n = len(s)
    k = max(0, min(n - 1, int(n * limit)))
    lo, hi = s[k], s[n - 1 - k]
    return [min(max(v, lo), hi) for v in values]


def raw_beta(asset: list, benchmark: list, winsor_limit: float = 0.0) -> tuple[float | None, dict]:
    """β = cov(r_a, r_b) / var(r_b). OLS без библиотек."""
    pairs = _clean_pairs(asset, benchmark)
    diag = {"observations": len(pairs)}
    if len(pairs) < 2:
        diag["reason"] = "not_enough_observations"
        return None, diag

    ra = [p[0] for p in pairs]
    rb = [p[1] for p in pairs]
    if winsor_limit:
        ra, rb = winsorize(ra, winsor_limit), winsorize(rb, winsor_limit)

    n = len(ra)
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((a - ma) * (b - mb) for a, b in zip(ra, rb)) / (n - 1)
    var = sum((b - mb) ** 2 for b in rb) / (n - 1)
    if var <= 0:
        diag["reason"] = "benchmark_variance_zero"
        return None, diag
    beta = cov / var
    if not math.isfinite(beta):
        diag["reason"] = "beta_not_finite"
        return None, diag
    diag.update({"covariance": cov, "benchmark_variance": var, "raw_beta": round(beta, 4)})
    return beta, diag


def adjusted_beta(beta: float, weight_raw: float = 0.67, weight_market: float = 0.33) -> float:
    """Blume-shrinkage к единице: β_adj = w×β + (1−w)×1.

    Сырая бета по короткому ряду смещена и плохо предсказывает саму себя в
    следующем периоде; сжатие к рынку — стандартная поправка на это.
    """
    return weight_raw * float(beta) + weight_market * 1.0


def liquidity_premium(median_turnover_rub: float | None, cfg: dict) -> tuple[float, str]:
    """Премия за ликвидность по ступеням оборота.

    Ступени, а не непрерывная формула: непрерывная создала бы ложное
    впечатление точности там, где её нет. Границы вынесены в конфиг.
    """
    tiers = cfg.get("tiers") or []
    if median_turnover_rub is None or not math.isfinite(float(median_turnover_rub)):
        return float(cfg.get("unknown", 0.0)), "оборот неизвестен — премия по верхней ступени"
    t = float(median_turnover_rub)
    for tier in tiers:
        if t >= float(tier["min_turnover_rub"]):
            return float(tier["premium"]), tier.get("label", "")
    return float(cfg.get("unknown", 0.0)), "оборот ниже всех порогов"


def issuer_premium(flags: list[str], cfg: dict) -> tuple[float, list[str]]:
    """Специфическая премия эмитента — только по объявленным флагам.

    Ручное «накинуть пару процентов, потому что чувствую» запрещено (§8.4):
    каждый пункт премии обязан иметь имя, величину из конфига и попадать в
    карточку банка.
    """
    table = cfg.get("flags") or {}
    total, applied = 0.0, []
    for f in flags or []:
        if f in table:
            total += float(table[f]["premium"])
            applied.append(table[f].get("label", f))
    cap = cfg.get("max_total")
    if cap is not None:
        total = min(total, float(cap))
    return total, applied


def cost_of_equity(risk_free: float,
                   beta_adj: float,
                   equity_risk_premium: float,
                   liquidity_prem: float = 0.0,
                   issuer_prem: float = 0.0) -> float | None:
    """Собрать k_e. Возврат None вместо нефинитного значения."""
    parts = (risk_free, beta_adj, equity_risk_premium, liquidity_prem, issuer_prem)
    if not all(isinstance(p, (int, float)) and math.isfinite(float(p)) for p in parts):
        return None
    ke = float(risk_free) + float(beta_adj) * float(equity_risk_premium) \
        + float(liquidity_prem) + float(issuer_prem)
    return ke if math.isfinite(ke) else None


def build(ticker: str,
          asset_returns: list,
          benchmark_returns: list,
          risk_free: float,
          cfg: dict,
          median_turnover_rub: float | None = None,
          issuer_flags: list[str] | None = None,
          sector_beta: float | None = None) -> dict:
    """Полная сборка k_e по банку с раздельными компонентами и диагностикой.

    Если бета ненадёжна, подставляется СЕКТОРНАЯ с явным флагом и понижением
    качества — но не «случайное разумное число» (§8.3).
    """
    bcfg = cfg["beta"]
    min_obs = int(bcfg["min_observations"])
    beta, bdiag = raw_beta(asset_returns, benchmark_returns, float(bcfg.get("winsor_limit", 0.0)))

    beta_source, warnings = "own", []
    if beta is None or bdiag.get("observations", 0) < min_obs:
        if sector_beta is None:
            return {"ok": False, "reason": "beta_unavailable", "beta_diagnostics": bdiag,
                    "ticker": ticker}
        beta, beta_source = float(sector_beta), "sector"
        warnings.append(f"Использована секторная beta: собственных наблюдений "
                        f"{bdiag.get('observations', 0)} < {min_obs}")

    b_adj = adjusted_beta(beta, float(bcfg["shrink_weight_raw"]), float(bcfg["shrink_weight_market"]))
    lp, lp_note = liquidity_premium(median_turnover_rub, cfg["liquidity_premium"])
    isp, isp_applied = issuer_premium(issuer_flags or [], cfg["issuer_premium"])
    erp = float(cfg["equity_risk_premium"])

    ke = cost_of_equity(risk_free, b_adj, erp, lp, isp)
    if ke is None:
        return {"ok": False, "reason": "cost_of_equity_not_finite", "ticker": ticker}

    if lp > 0:
        warnings.append(f"Премия за ликвидность +{lp * 100:.1f} п.п.: {lp_note}")
    for label in isp_applied:
        warnings.append(f"Премия за риск эмитента: {label}")

    return {
        "ok": True,
        "ticker": ticker,
        "cost_of_equity": round(ke, 6),
        "components": {
            "risk_free": round(float(risk_free), 6),
            "beta_raw": round(float(beta), 4),
            "beta_adjusted": round(b_adj, 4),
            "beta_source": beta_source,
            "equity_risk_premium": round(erp, 6),
            "liquidity_premium": round(lp, 6),
            "issuer_premium": round(isp, 6),
        },
        "beta_diagnostics": bdiag,
        "warnings": warnings,
    }


def scenarios(base_ke: float, cfg: dict) -> dict:
    """bull/base/bear как сдвиги требуемой доходности, а не прибыли (§8.5)."""
    shift = float(cfg["scenario_shift_pp"]) / 100.0
    return {
        "bull": round(base_ke - shift, 6),      # ниже риск → ниже требуемая доходность
        "base": round(base_ke, 6),
        "bear": round(base_ke + shift, 6),
    }


def sector_beta_from(betas: list[float]) -> float | None:
    """Медианная бета сектора — запасной вариант для банков с короткой историей."""
    vals = [float(b) for b in betas if b is not None and math.isfinite(float(b))]
    return float(median(vals)) if vals else None
