#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сборка оценки банков через Residual Income → site/cbr/valuation_v2.json.

Пайплайн ДОБАВЛЯЕТСЯ к существующему, а не заменяет его (§28): valuation.json с
регуляторным контуром продолжает жить и обновляться. Здесь считается второй,
независимый контур — фундаментальная оценка публичной группы.

Источники (все уже есть в проекте, ничего нового не выдумывается):
  • капитал / прибыль / дивиденды  → site/site_financials.json (фундамент-слой)
  • безрисковая ставка             → site/bonds/chart_data.json, точка КБД ОФЗ
  • бенчмарк для беты              → site/marketsaw.json, индекс MCFTR
  • доходности акций               → site/returns.json (месячные)
  • цена, капитализация, акции     → site/cbr/valuation.json (первый контур)

ГЛАВНОЕ ОГРАНИЧЕНИЕ, которое нельзя замолчать: база капитала берётся из
фундамент-слоя (SmartLab), а не из первичной отчётности МСФО. По §4.3 это
означает потолок статуса `limited` и ЗАПРЕТ публиковать справедливую цену акции.
Публикуются справедливый P/BV и разложение — величины, устойчивые к масштабной
ошибке базы, потому что она сокращается в отношении V₀/BV₀.

ТОЛЬКО stdlib: workflow update-cbr-banks.yml не делает pip install.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone
from statistics import median

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
REPO = os.path.dirname(os.path.dirname(HERE))

from cost_of_equity import build as build_coe, raw_beta, sector_beta_from   # noqa: E402
from forecast import (build_forecast, clean_surplus_check, normalized_roe,  # noqa: E402
                      sustainable_growth)
from residual_income import justified_pbv_single_stage, value_equity        # noqa: E402

SITE = os.path.join(REPO, "site")
OUT = os.path.join(SITE, "cbr", "valuation_v2.json")
CONFIG = os.path.join(HERE, "valuation_config.json")
BANKS_CONFIG = os.path.join(HERE, "banks_config.json")
SCHEMA_VERSION = "2.0.0"


def log(m: str) -> None:
    sys.stderr.write(f"[banks-v2] {m}\n")


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── Источники ────────────────────────────────────────────────────────────────

def field_series(fundamentals: dict, ticker: str, field: str) -> dict[int, float]:
    """Ряд {год: значение} из фундамент-слоя, приведённый к рублям.

    Значения лежат в `values: [{year, value}]`, а масштаб — в `scale`. Забыть
    про scale значит ошибиться в миллион раз, поэтому он применяется здесь один раз.
    """
    rec = fundamentals.get(ticker) or {}
    for group in rec.values():
        if not isinstance(group, list):
            continue
        for row in group:
            if row.get("field") != field:
                continue
            scale = float(row.get("scale") or 1)
            return {int(v["year"]): float(v["value"]) * scale
                    for v in (row.get("values") or []) if v.get("value") is not None}
    return {}


def field_source(fundamentals: dict, ticker: str, field: str) -> dict:
    rec = fundamentals.get(ticker) or {}
    for group in rec.values():
        if not isinstance(group, list):
            continue
        for row in group:
            if row.get("field") == field:
                return {"name": row.get("source_name"), "status": row.get("source_status"),
                        "url": row.get("source_url")}
    return {}


def median_turnover(tickers: list[str], days: int = 20) -> dict[str, float]:
    """Медианный дневной оборот акции за последние торговые дни, ₽.

    Без него премия за ликвидность начисляется по верхней ступени ВСЕМ — включая
    Сбербанк, самую ликвидную бумагу рынка. Это не консерватизм, а ошибка: 2 п.п.
    лишней ставки дисконтирования занижают оценку на десятки процентов.
    """
    import urllib.request

    out: dict[str, float] = {}
    for tk in tickers:
        url = (f"https://iss.moex.com/iss/history/engines/stock/markets/shares/boards/TQBR/"
               f"securities/{tk}.json?iss.meta=off&iss.only=history"
               f"&history.columns=TRADEDATE,VALUE&sort_order=desc&limit={days}")
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                d = json.loads(r.read().decode("utf-8"))
            rows = (d.get("history") or {}).get("data") or []
            vals = [float(v) for _, v in rows if v not in (None, "")]
            if vals:
                out[tk] = float(median(vals))
        except Exception as e:  # noqa: BLE001
            log(f"оборот {tk} недоступен: {str(e)[:60]}")
    return out


def risk_free_from_gcurve(tenor_years: float) -> tuple[float | None, dict]:
    """Точка КБД ОФЗ на срок, сопоставимый с прогнозным периодом (§8.2).

    Ключевая ставка сюда НЕ подставляется: она про сегодняшний овернайт, а не про
    требуемую доходность на пять лет вперёд, и на растущей кривой занижает ставку.
    """
    path = os.path.join(SITE, "bonds", "chart_data.json")
    if not os.path.exists(path):
        return None, {"reason": "g_curve_unavailable"}
    d = load(path)
    curve = (d.get("chart") or d).get("ofz_curve") or []
    pts = [(float(p["t"]), float(p["yield"])) for p in curve
           if p.get("t") is not None and p.get("yield") is not None]
    if not pts:
        return None, {"reason": "g_curve_empty"}
    pts.sort()
    exact = [y for t, y in pts if abs(t - tenor_years) < 1e-9]
    if exact:
        value, method = exact[0], "точка кривой"
    else:                                      # линейная интерполяция между соседями
        lo = max([p for p in pts if p[0] <= tenor_years], default=pts[0])
        hi = min([p for p in pts if p[0] >= tenor_years], default=pts[-1])
        if lo[0] == hi[0]:
            value, method = lo[1], "ближайшая точка"
        else:
            w = (tenor_years - lo[0]) / (hi[0] - lo[0])
            value, method = lo[1] + w * (hi[1] - lo[1]), "линейная интерполяция"
    asof = str((d.get("meta") or {}).get("updated") or "")[:10]
    return value / 100.0, {"tenor_years": tenor_years, "method": method,
                           "source": "КБД ОФЗ, MOEX ISS", "as_of": asof,
                           "value_pct": round(value, 3)}


def mcftr_monthly(months: list[str]) -> tuple[list, dict]:
    """Месячные доходности индекса полной доходности MCFTR под сетку returns.json.

    Именно полной доходности (§8.3): бета к ценовому индексу систематически
    смещена, потому что дивиденды в знаменателе отсутствуют, а в акции банка — есть.
    """
    path = os.path.join(SITE, "marketsaw.json")
    if not os.path.exists(path):
        return [], {"reason": "marketsaw_unavailable"}
    d = load(path)
    if str(d.get("index") or "").upper() != "MCFTR":
        return [], {"reason": f"unexpected_index:{d.get('index')}"}
    closes: dict[str, float] = {}
    for row in d.get("series") or []:
        if not row or len(row) < 2 or row[1] is None:
            continue
        closes[str(row[0])[:7]] = float(row[1])      # последнее значение месяца
    out = []
    for i, m in enumerate(months):
        prev = months[i - 1] if i else None
        a, b = closes.get(prev) if prev else None, closes.get(m)
        out.append((b / a - 1.0) if (a and b) else None)
    have = sum(1 for x in out if x is not None)
    return out, {"index": "MCFTR", "source": "MOEX ISS (marketsaw)",
                 "months_covered": have, "months_requested": len(months)}


# ── Оценка одного банка ──────────────────────────────────────────────────────

def payout_from_history(net_income: dict[int, float], dividends: dict[int, float],
                        years: int = 5) -> tuple[float | None, dict]:
    """Медианный payout за последние годы, а не круглое «50%».

    Круглое число было бы предпосылкой, выданной за наблюдение.
    """
    ratios = []
    for y in sorted(net_income)[-years:]:
        ni, dv = net_income.get(y), dividends.get(y)
        if ni and ni > 0 and dv is not None and dv >= 0:
            ratios.append(min(dv / ni, 1.0))
    if not ratios:
        return None, {"observations": 0, "reason": "no_dividend_history"}
    return float(median(ratios)), {"observations": len(ratios),
                                   "median": round(float(median(ratios)), 4)}


def value_one(ticker: str, bank_cfg: dict, cfg: dict, fundamentals: dict,
              returns: dict, bench: list, rf: float, sector_beta: float | None,
              market: dict) -> dict:
    """Полная оценка банка. Возврат всегда со статусом — «молча пропустить» нельзя."""
    fc, qc = cfg["forecast"], cfg["quality"]
    out: dict = {"ticker": ticker, "name": bank_cfg.get("name"), "warnings": [],
                 "status": "unavailable", "reason": None}

    equity = field_series(fundamentals, ticker, "total_equity")
    net_income = field_series(fundamentals, ticker, "net_income")
    dividends = field_series(fundamentals, ticker, "dividends")
    out["equity_source"] = field_source(fundamentals, ticker, "total_equity")

    if not equity:
        out["reason"] = "no_book_value"
        return out
    last_year = max(equity)
    bv0 = equity[last_year]
    out["book_value"] = {"year": last_year, "value_rub": round(bv0, 2)}
    if bv0 <= 0:
        out["reason"] = "book_value_not_positive"
        return out

    # ── ROE: история → нормализованный → терминальный
    roe_hist = sorted((y, net_income[y] / equity[y - 1])
                      for y in net_income if (y - 1) in equity and equity[y - 1])
    norm_roe, roe_diag = normalized_roe(roe_hist, int(fc["min_roe_history_years"]))
    if norm_roe is None:
        out["reason"] = "roe_history_too_short"
        out["roe_diagnostics"] = roe_diag
        return out
    terminal_roe = max(float(fc["terminal_roe_floor"]),
                       min(norm_roe, float(fc["terminal_roe_sector_cap"])))
    if terminal_roe < norm_roe:
        out["warnings"].append(
            f"Терминальный ROE ограничен секторным потолком {fc['terminal_roe_sector_cap']:.0%}: "
            f"историческая медиана {norm_roe:.1%} не может держаться вечно")
    out["roe"] = {"last": round(roe_hist[-1][1], 6), "normalized": round(norm_roe, 6),
                  "terminal": round(terminal_roe, 6), "diagnostics": roe_diag}
    # Медиана за всю историю не знает, что банк мог структурно измениться. Если
    # последний год далеко от неё, оценка опирается на прошлое, а не на настоящее —
    # пользователь обязан это видеть, иначе расхождение с рынком выглядит как «рынок неправ».
    last_roe = roe_hist[-1][1]
    if norm_roe > 0 and abs(last_roe - norm_roe) / norm_roe > 0.5:
        out["warnings"].append(
            f"Нормализованный ROE {norm_roe:.1%} — медиана за {roe_diag['observations']} лет, "
            f"а последний год {last_roe:.1%}. Модель опирается на долгую историю: если банк "
            "структурно изменился, оценка занижена (или завышена) относительно новой реальности")

    # ── payout
    payout, pay_diag = payout_from_history(net_income, dividends)
    if payout is None:
        payout = float(1.0 - terminal_roe / max(terminal_roe, 1e-9))  # = 0.0
        payout = 0.0
        out["warnings"].append("Дивидендной истории нет: payout принят нулевым, "
                               "весь доход реинвестируется — рост капитала максимальный")
    out["payout"] = {"value": round(payout, 4), "diagnostics": pay_diag}

    # ── стоимость капитала
    flags = list(bank_cfg.get("issuer_flags") or [])
    if (out["equity_source"].get("status") or "").endswith("fallback"):
        flags.append("secondary_equity_source")
    coe = build_coe(ticker, returns.get(ticker) or [], bench, rf, cfg["cost_of_equity"],
                    market.get("median_turnover_rub"), flags, sector_beta)
    if not coe.get("ok"):
        out["reason"] = coe.get("reason")
        out["beta_diagnostics"] = coe.get("beta_diagnostics")
        return out
    ke = float(coe["cost_of_equity"])
    out["cost_of_equity"] = coe
    out["warnings"].extend(coe.get("warnings") or [])

    # ── прогноз и оценка
    g_raw = sustainable_growth(terminal_roe, payout)
    g = min(g_raw, float(fc["terminal_growth_cap"]))
    if g < g_raw:
        out["warnings"].append(
            f"Устойчивый рост ограничен {g:.0%}: удержание всей прибыли давало бы {g_raw:.0%} "
            "в год вечно — быстрее роста номинальной экономики")
    rows = build_forecast(bv0, roe_hist[-1][1], terminal_roe, payout, ke,
                          int(fc["years"]), float(fc["fade_lambda"]), last_year + 1)
    val = value_equity(bv0, rows, terminal_roe, ke, g)
    out["growth_terminal"] = round(g, 6)
    if not val.ok:
        out["reason"] = val.reason
        return out

    # Неотрицательность капитала — не косметика: акционер не обязан довносить деньги,
    # его убыток ограничен вложенным. Оценка «стоит около нуля или меньше» означает,
    # что набор предпосылок непригоден, и по §6.2 её нельзя публиковать как результат.
    if val.fair_pbv is not None and val.fair_pbv <= 0.05:
        out["reason"] = "implausible_valuation_assumptions"
        out["diagnostics"] = {"fair_pbv_raw": round(val.fair_pbv, 4),
                              "cost_of_equity": round(ke, 6),
                              "terminal_roe": round(terminal_roe, 6),
                              "growth_terminal": round(g, 6)}
        out["warnings"].append(
            "Предпосылки дают стоимость капитала около нуля: ROE устойчиво ниже требуемой "
            "доходности при полном реинвестировании. Это признак непригодных предпосылок, "
            "а не вывод о бесполезности акций — оценка не публикуется")
        return out

    out["valuation"] = val.as_dict()
    out["single_stage_benchmark"] = justified_pbv_single_stage(terminal_roe, g, ke)

    # ── clean surplus
    cs = clean_surplus_check(sorted(equity.items()), net_income, dividends,
                             float(fc["clean_surplus_tolerance"]))
    out["clean_surplus"] = cs
    if cs.get("status") == "broken":
        out["warnings"].append(
            "Прирост капитала в истории не объясняется прибылью и дивидендами: значимы "
            "прочий совокупный доход, эмиссии или выкупы. В прогнозе они НЕ моделируются")

    # ── рынок: только сравнение, не якорь оценки
    mcap = market.get("mcap_rub")
    if mcap and bv0:
        out["market"] = {"mcap_rub": mcap, "price": market.get("price"),
                         "price_as_of": market.get("price_as_of"),
                         "actual_pbv": round(mcap / bv0, 4)}

    # ── доля терминала
    share = val.terminal_share
    if abs(share) > float(qc["max_terminal_share"]):
        out["warnings"].append(
            f"Вклад терминальной стоимости — {share:+.0%} балансового капитала: результат "
            "держится на предпосылке об устойчивом ROE, а не на прогнозном периоде")

    out.update(quality(out, cfg, coe, cs))
    return out


# ── Гейт качества (§14) ──────────────────────────────────────────────────────

def quality(row: dict, cfg: dict, coe: dict, cs: dict) -> dict:
    """Объяснимый score: не одно число, а разложение по компонентам."""
    w = cfg["quality"]["weights"]
    comp, notes = {}, []

    primary = (row.get("equity_source", {}).get("status") or "") == "official_ifrs"
    comp["source_quality"] = w["source_quality"] if primary else int(w["source_quality"] * 0.4)
    if not primary:
        notes.append("База капитала — вторичный источник (фундамент-слой), не первичная отчётность МСФО")

    comp["freshness"] = w["freshness"]
    fin_year = (row.get("book_value") or {}).get("year")
    if fin_year and fin_year < date.today().year - 1:
        comp["freshness"] = int(w["freshness"] * 0.5)
        notes.append(f"Последний год отчётности — {fin_year}")

    match = row.get("perimeter_match") or "unknown"
    comp["perimeter_match"] = {"exact": w["perimeter_match"], "close": int(w["perimeter_match"] * 0.8),
                               "partial": int(w["perimeter_match"] * 0.5),
                               "material_mismatch": int(w["perimeter_match"] * 0.2),
                               "unknown": 0}.get(match, 0)

    comp["share_count_quality"] = 0 if not (row.get("market") or {}).get("mcap_rub") else w["share_count_quality"]

    cs_status = cs.get("status")
    comp["clean_surplus_quality"] = {"ok": w["clean_surplus_quality"],
                                     "noisy": int(w["clean_surplus_quality"] * 0.5),
                                     "broken": 0, "unknown": 0}.get(cs_status, 0)

    beta_own = (coe.get("components") or {}).get("beta_source") == "own"
    comp["beta_quality"] = w["beta_quality"] if beta_own else int(w["beta_quality"] * 0.4)

    share = ((row.get("valuation") or {}).get("decomposition") or {}).get("terminal_pv_over_book")
    if share is None:
        comp["forecast_quality"] = 0
    elif abs(share) > float(cfg["quality"]["max_terminal_share"]):
        comp["forecast_quality"] = int(w["forecast_quality"] * 0.3)
        notes.append("Терминал доминирует в оценке")
    else:
        comp["forecast_quality"] = w["forecast_quality"]

    score = sum(comp.values())

    # Потолок статуса: без первичной МСФО полная оценка запрещена (§4.3, вариант А)
    status = "limited"
    if not primary:
        notes.append("Статус ограничен: полная оценка требует первичной отчётности МСФО")
    elif score >= 80 and cs_status == "ok" and beta_own and match in ("exact", "close"):
        status = "full"
    if match == "material_mismatch":
        notes.append("Периметр группы существенно шире банковского юрлица")

    return {"status": status,
            "quality": {"score": score, "max": sum(w.values()), "components": comp, "notes": notes}}


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    cfg = load(CONFIG)
    banks_cfg = load(BANKS_CONFIG)
    fundamentals = load(os.path.join(SITE, "site_financials.json"))["fundamentals"]
    returns_doc = load(os.path.join(SITE, "returns.json"))
    returns, months = returns_doc["data"], returns_doc["meta"]["months"]

    rf, rf_meta = risk_free_from_gcurve(float(cfg["cost_of_equity"]["risk_free"]["tenor_years"]))
    if rf is None:
        log(f"СТОП: безрисковая ставка недоступна ({rf_meta.get('reason')})")
        return 1
    log(f"безрисковая ставка {rf:.2%} ({rf_meta['method']}, {rf_meta['tenor_years']}л, {rf_meta['as_of']})")

    bench, bench_meta = mcftr_monthly(months)
    if not bench:
        log(f"СТОП: бенчмарк недоступен ({bench_meta.get('reason')})")
        return 1
    log(f"бенчмарк {bench_meta['index']}: {bench_meta['months_covered']}/{bench_meta['months_requested']} месяцев")

    bank_list = banks_cfg["banks"]
    turnover = median_turnover([b["ticker"] for b in bank_list])
    log(f"медианный оборот получен по {len(turnover)}/{len(bank_list)} бумагам")

    # рыночные данные берём из первого контура — он их уже собрал с MOEX
    market_by_ticker = {}
    v1_path = os.path.join(SITE, "cbr", "valuation.json")
    if os.path.exists(v1_path):
        for b in load(v1_path).get("banks") or []:
            market_by_ticker[b["ticker"]] = {
                "mcap_rub": b.get("mcap_rub"), "price": b.get("price"),
                "price_as_of": (b.get("vintages") or {}).get("moex"),
                "median_turnover_rub": turnover.get(b["ticker"]),
            }
    own_betas = {}
    for b in bank_list:
        beta, _ = raw_beta(returns.get(b["ticker"]) or [], bench,
                           float(cfg["cost_of_equity"]["beta"]["winsor_limit"]))
        if beta is not None:
            own_betas[b["ticker"]] = beta
    sector_beta = sector_beta_from(list(own_betas.values()))
    log(f"секторная бета (медиана по {len(own_betas)} банкам): "
        f"{sector_beta:.3f}" if sector_beta else "секторная бета недоступна")

    rows = []
    for b in bank_list:
        row = value_one(b["ticker"], b, cfg, fundamentals, returns, bench, rf,
                        sector_beta, market_by_ticker.get(b["ticker"], {}))
        rows.append(row)
        if row.get("valuation"):
            log(f"{row['ticker']:6} P/BV_fair={row['valuation']['fair_pbv']:.2f} "
                f"k_e={row['cost_of_equity']['cost_of_equity']:.1%} "
                f"качество={row['quality']['score']} статус={row['status']}")
        else:
            log(f"{row['ticker']:6} оценка недоступна: {row.get('reason')}")

    valued = [r for r in rows if r.get("valuation")]
    if not valued:
        log("СТОП: ни один банк не оценён — не публикуем")
        return 1

    payload = {
        "meta": {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "model": "Residual Income",
            "currency": "RUB",
            "banks_count": len(rows),
            "banks_valued": len(valued),
            "risk_free": rf_meta,
            "benchmark": bench_meta,
            "sector_beta": round(sector_beta, 4) if sector_beta else None,
            "publication": cfg["publication"],
            "disclaimer": (
                "Фундаментальная оценка публичной группы по модели остаточного дохода. "
                "База капитала — фундамент-слой сайта, а не первичная отчётность МСФО, поэтому "
                "статус ограничен и справедливая цена акции НЕ публикуется. Показатели "
                "регуляторного контура (формы 102/123/135) в этой модели НЕ используются. "
                "Не является индивидуальной инвестиционной рекомендацией."),
        },
        "assumptions": {"forecast": cfg["forecast"], "cost_of_equity": cfg["cost_of_equity"]},
        "banks": rows,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    log(f"OK → {OUT}: оценено {len(valued)}/{len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
