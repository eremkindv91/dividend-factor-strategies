#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Лёгкая валидация контрактов сгенерированных JSON сайта. Чистый stdlib (без pandas/numpy).
Запускается в update.yml ПЕРЕД публикацией и в bonds_update.yml после генерации bonds.

Падает (exit 1) при КРИТИЧЕСКИХ ошибках (битый/несогласованный контракт → не публикуем).
WARNING печатает для некритичных проблем (публикуем, но видно в логах CI).

CLI:
  python scripts/validate_site_data.py              # все файлы в site/
  python scripts/validate_site_data.py bonds        # только bonds/*.json
  python scripts/validate_site_data.py data         # только data.json/returns.json
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from urllib.parse import urlparse

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(REPO, "site")

ERRORS: list[str] = []
WARNINGS: list[str] = []
CURRENT_SEL = "all"
OFFICIAL_RATING_HOSTS = {"acra-ratings.ru", "www.acra-ratings.ru", "raexpert.ru", "www.raexpert.ru", "ratings.ru", "www.ratings.ru"}
OFFICIAL_RATING_AGENCIES = {"АКРА", "Эксперт РА", "НКР"}


def err(msg: str) -> None:
    ERRORS.append(msg)


def warn(msg: str) -> None:
    WARNINGS.append(msg)


def load(path: str):
    full = os.path.join(SITE, path)
    if not os.path.exists(full):
        return None
    try:
        with open(full, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        err(f"{path}: невалидный JSON ({e})")
        return None


def is_num_or_na(x) -> bool:
    return isinstance(x, (int, float)) or x is None or x == "нет данных"


def as_date(s):
    try:
        return date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


def as_datetime(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


# ── data.json ────────────────────────────────────────────────────────────────
def check_data() -> None:
    d = load("data.json")
    if d is None:
        warn("data.json отсутствует (gitignored/не сгенерирован) — пропуск")
        return
    meta, tk = d.get("meta"), d.get("tickers")
    if not isinstance(meta, dict):
        err("data.json: нет meta"); return
    if not isinstance(tk, list) or not tk:
        err("data.json: tickers пуст/не список"); return
    if meta.get("n_total") not in (None, len(tk)):
        err(f"data.json: n_total={meta.get('n_total')} ≠ len(tickers)={len(tk)}")
    req = ("ticker", "status", "price", "dividend_forecast")
    miss = [k for k in req if k not in tk[0]]
    if miss:
        err(f"data.json: у тикера нет полей {miss}")
    bad_num = 0
    for t in tk:
        for k in ("price", "dividend_forecast", "dividend_yield_expected", "verdict_score"):
            if k in t and not is_num_or_na(t[k]):
                bad_num += 1
    if bad_num:
        err(f"data.json: {bad_num} числовых полей не number/None/'нет данных'")
    # даты: price_asof не позже даты расчёта (обновлено/generated_at)
    gen = as_date(meta.get("generated_at") or meta.get("обновлено"))
    pa = as_date(meta.get("price_asof"))
    if gen and pa and pa > gen:
        err(f"data.json: price_asof {pa} позже даты расчёта {gen}")
    # согласованность source_ok / prices_stale
    if meta.get("source_ok") is True and meta.get("prices_stale") is True:
        warn("data.json: source_ok=true, но prices_stale=true (проверь логику)")
    if meta.get("source_ok") is False and meta.get("prices_stale") is False:
        err("data.json: source_ok=false, но prices_stale=false — несогласованно")
    print(f"  data.json: {len(tk)} тикеров, price_asof={meta.get('price_asof')}, source_ok={meta.get('source_ok')}")


# ── returns.json ─────────────────────────────────────────────────────────────
def check_returns() -> None:
    d = load("returns.json")
    if d is None:
        warn("returns.json отсутствует — пропуск")
        return
    months = (d.get("meta") or {}).get("months")
    if not isinstance(months, list) or not months:
        err("returns.json: нет meta.months"); return
    n = len(months)
    data = d.get("data") or {}
    bad = [tk for tk, s in data.items() if not isinstance(s, list) or len(s) != n]
    if bad:
        err(f"returns.json: {len(bad)} рядов data длиной ≠ {n} (напр. {bad[:3]})")
    div = d.get("div") or {}
    bad_div = [tk for tk, s in div.items() if not isinstance(s, list) or len(s) != n]
    if bad_div:
        err(f"returns.json: {len(bad_div)} рядов div длиной ≠ {n}")
    print(f"  returns.json: months={n}, рядов data={len(data)}, div={len(div)}")


# ── marketsaw.json ───────────────────────────────────────────────────────────
def check_marketsaw() -> None:
    d = load("marketsaw.json")
    if d is None:
        warn("marketsaw.json отсутствует — пропуск")
        return
    cp = d.get("current_phase")
    if not cp:
        err("marketsaw.json: нет current_phase"); return
    if not d.get("data_last"):
        err("marketsaw.json: нет data_last")
    series = d.get("series") or []
    if not series:
        err("marketsaw.json: series пуст"); return
    last_close = series[-1][1] if isinstance(series[-1], list) else None
    if last_close is not None and cp.get("current_price") is not None:
        if abs(float(cp["current_price"]) - float(last_close)) > 0.01:
            err(f"marketsaw.json: current_price {cp['current_price']} ≠ последней точке series {last_close}")
    sd = d.get("stale_days")
    if sd is not None and (not isinstance(sd, (int, float)) or sd < 0):
        err(f"marketsaw.json: stale_days некорректен: {sd}")
    if isinstance(sd, (int, float)) and d.get("stale") is False and sd > 5:
        warn(f"marketsaw.json: stale=false, но stale_days={sd}>5")
    print(f"  marketsaw.json: фаза={cp.get('direction')}, data_last={d.get('data_last')}, точек={len(series)}")


def check_market_history() -> None:
    d = load("market_history.json")
    if d is None:
        if CURRENT_SEL == "market_history":
            err("market_history.json отсутствует")
        else:
            warn("market_history.json отсутствует — пропуск")
        return
    if as_datetime(d.get("generated_at")) is None:
        err("market_history.json: generated_at не ISO datetime")
    instruments = d.get("instruments")
    if not isinstance(instruments, list) or not instruments:
        err("market_history.json: instruments пуст/не список")
        return
    ids = {row.get("id") for row in instruments if isinstance(row, dict)}
    missing = {"MCFTR", "IMOEX", "RTSI"} - ids
    if missing:
        err(f"market_history.json: нет core indices {sorted(missing)}")
    expected_columns = ["date", "open", "high", "low", "close", "sma20", "sma50", "sma200"]
    for item in instruments:
        iid = item.get("id")
        if item.get("columns") != expected_columns:
            err(f"market_history.json: {iid} — неизвестный columns contract")
        host = (urlparse(str(item.get("source_url") or "")).hostname or "").lower()
        if host != "iss.moex.com" or item.get("source") != "MOEX ISS":
            err(f"market_history.json: {iid} — источник не официальный MOEX ISS")
        series = item.get("series") or []
        if len(series) < 220:
            err(f"market_history.json: {iid} — только {len(series)} точек")
            continue
        dates = [row[0] for row in series if isinstance(row, list) and len(row) == len(expected_columns)]
        if len(dates) != len(series) or dates != sorted(dates) or len(set(dates)) != len(dates):
            err(f"market_history.json: {iid} — даты/форма series некорректны")
            continue
        bad_ohlc = sum(
            1 for row in series
            if not all(isinstance(row[i], (int, float)) for i in range(1, 5))
            or not (row[3] <= min(row[1], row[4]) <= max(row[1], row[4]) <= row[2])
        )
        if bad_ohlc:
            err(f"market_history.json: {iid} — {bad_ohlc} битых OHLC строк")
        summary = item.get("summary") or {}
        if abs(float(summary.get("last", 0)) - float(series[-1][4])) > 1e-6:
            err(f"market_history.json: {iid} — summary.last не совпадает с последним close")
        rsi = summary.get("rsi14")
        if not isinstance(rsi, (int, float)) or not (0 <= rsi <= 100):
            err(f"market_history.json: {iid} — RSI(14) вне [0,100]")
        for window in (20, 60, 252):
            low, high, last = summary.get(f"low{window}"), summary.get(f"high{window}"), summary.get("last")
            if not all(isinstance(value, (int, float)) for value in (low, high, last)) or not low <= last <= high:
                err(f"market_history.json: {iid} — диапазон {window}d не содержит last")
        last_date = as_date(item.get("data_last"))
        if last_date and (date.today() - last_date).days > 10:
            warn(f"market_history.json: {iid} — данные старше 10 дней ({last_date})")
    print(f"  market_history.json: {len(instruments)} инструментов, errors={len(d.get('errors') or [])}")


# ── marlamov.json ────────────────────────────────────────────────────────────
def check_marlamov() -> None:
    d = load("marlamov.json")
    if d is None:
        warn("marlamov.json отсутствует — пропуск")
        return
    meta, rows = d.get("meta") or {}, d.get("rows") or []
    if not rows:
        err("marlamov.json: rows пуст"); return
    rfr = meta.get("rfr")
    if not isinstance(rfr, (int, float)) or not (0.03 <= rfr <= 0.40):
        err(f"marlamov.json: RFR вне диапазона [3%,40%]: {rfr}")
    if "regime" not in meta:
        warn("marlamov.json: нет meta.regime")
    bad = sum(1 for r in rows if r.get("yield2") is not None and not (-1 < r["yield2"] < 5))
    if bad:
        err(f"marlamov.json: {bad} строк с абсурдным yield2")
    gross_bad = sum(
        1 for r in rows
        if r.get("gross_yield1") is not None and not (0 < r["gross_yield1"] < 1)
    )
    if gross_bad:
        err(f"marlamov.json: {gross_bad} строк с абсурдным gross_yield1")

    backtest = d.get("backtest") or {}
    if backtest.get("status") == "unavailable":
        warn("marlamov.json: research backtest временно недоступен")
    elif backtest:
        metrics = backtest.get("metrics") or {}
        required = [
            "cagr", "volatility", "sharpe", "sortino", "max_drawdown", "calmar",
            "alpha_vs_mcftr", "beta_vs_mcftr", "excess_return_vs_rfr",
            "average_turnover", "hit_rate", "profit_factor", "rebalances", "months",
        ]
        missing = [key for key in required if not isinstance(metrics.get(key), (int, float))]
        if missing:
            err(f"marlamov.json: backtest без числовых метрик {missing}")
        if backtest.get("point_in_time") is not False:
            err("marlamov.json: lagged backtest должен явно иметь point_in_time=false")
        if not (backtest.get("limitations") or []):
            err("marlamov.json: backtest не раскрывает ограничения")
        parameters = backtest.get("parameters") or {}
        if not isinstance(parameters.get("entry_spread_pct"), (int, float)) or parameters["entry_spread_pct"] <= 0:
            err("marlamov.json: backtest без положительного entry_spread_pct")
        if metrics.get("months", 0) < 60:
            err(f"marlamov.json: слишком короткий backtest ({metrics.get('months')} мес.)")
        if not (backtest.get("series") or []):
            err("marlamov.json: backtest.series пуст")
    else:
        warn("marlamov.json: нет research backtest")
    bt_months = ((backtest.get("metrics") or {}).get("months") if backtest else 0) or 0
    print(f"  marlamov.json: строк={len(rows)}, RFR={rfr}, режим={meta.get('regime')}, backtest={bt_months} мес.")


# ── quality.json ────────────────────────────────────────────────────────────
def check_quality() -> None:
    d = load("quality.json")
    if d is None:
        if CURRENT_SEL == "quality":
            err("quality.json отсутствует")
        else:
            warn("quality.json отсутствует — пропуск")
        return
    meta, rows = d.get("meta") or {}, d.get("rows") or []
    if meta.get("schema_version") != "2.0":
        err(f"quality.json: неизвестный schema_version={meta.get('schema_version')!r}")
    if meta.get("methodology_version") != "ru_quality_sector_v2":
        err(f"quality.json: неизвестный methodology_version={meta.get('methodology_version')!r}")
    if not isinstance(rows, list) or not rows:
        err("quality.json: rows пуст/не список")
        return
    if meta.get("n_universe") != len(rows):
        err(f"quality.json: n_universe={meta.get('n_universe')} ≠ rows={len(rows)}")
    required = {"ticker", "issuer_id", "quality_model", "quality_model_label", "factor_definitions",
                "raw", "winsorized", "z", "coverage_ratio", "confidence", "eligible",
                "exclusion_reasons", "warnings", "provenance"}
    model_factors = {
        "industrial_core": {"roe", "debt_to_equity", "earnings_variability"},
        "bank_quality": {"bank_roe", "capital_headroom", "bank_profit_variability"},
        "it_quality": {"ebitda_margin", "fcf_margin", "net_debt_to_ebitda"},
    }
    for row in rows:
        missing = required - set(row)
        if missing:
            err(f"quality.json: {row.get('ticker')} без полей {sorted(missing)}")
        if row.get("eligible") and row.get("confidence") == "low":
            err(f"quality.json: {row.get('ticker')} low confidence, но eligible=true")
        coverage = row.get("coverage_ratio")
        if coverage not in (0.0, 0.33, 0.67, 1.0):
            err(f"quality.json: {row.get('ticker')} некорректный coverage_ratio={coverage}")
        if row.get("quality_score_sector") is not None and row.get("quality_z_absolute") is None:
            err(f"quality.json: {row.get('ticker')} sector score без absolute composite")
        model = row.get("quality_model")
        factor_keys = {item.get("key") for item in (row.get("factor_definitions") or [])}
        if model not in model_factors:
            err(f"quality.json: {row.get('ticker')} неизвестная модель {model!r}")
        elif factor_keys != model_factors[model] or set((row.get("raw") or {}).keys()) != model_factors[model]:
            err(f"quality.json: {row.get('ticker')} нарушен factor contract модели {model}")
        if model == "bank_quality":
            if (row.get("provenance") or {}).get("source_type") != "CBR_official_forms_102_123_135":
                err(f"quality.json: {row.get('ticker')} bank_quality без официального CBR provenance")
            if "debt_to_equity" in (row.get("raw") or {}):
                err(f"quality.json: {row.get('ticker')} bank_quality получил промышленный D/E")
        if model == "it_quality" and "roe" in (row.get("raw") or {}):
            err(f"quality.json: {row.get('ticker')} it_quality получил корпоративный ROE")
        if row.get("financial_model_required") and (row.get("raw") or {}).get("debt_to_equity") is not None:
            err(f"quality.json: {row.get('ticker')} financials получил промышленный D/E")
    present_models = {row.get("quality_model") for row in rows}
    missing_models = set(model_factors) - present_models
    if missing_models:
        err(f"quality.json: отсутствуют секторные модели {sorted(missing_models)}")
    actual_scored = sum(bool(row.get("score_eligible")) for row in rows)
    actual_eligible = sum(bool(row.get("eligible")) for row in rows)
    if meta.get("n_scored") != actual_scored:
        err(f"quality.json: n_scored={meta.get('n_scored')} ≠ {actual_scored}")
    if meta.get("n_eligible") != actual_eligible:
        err(f"quality.json: n_eligible={meta.get('n_eligible')} ≠ {actual_eligible}")
    actual_issuers = len({row.get("issuer_id") or row.get("ticker") for row in rows})
    if meta.get("n_issuers") != actual_issuers:
        err(f"quality.json: n_issuers={meta.get('n_issuers')} ≠ {actual_issuers}")
    backtest = d.get("backtest") or {}
    if backtest.get("status") == "unavailable" and backtest.get("point_in_time") is not False:
        err("quality.json: unavailable backtest должен явно иметь point_in_time=false")
    print(
        f"  quality.json: universe={len(rows)}, scored={actual_scored}, eligible={actual_eligible}, "
        f"coverage={meta.get('data_coverage')}, stale={meta.get('stale')}"
    )


# ── bonds/*.json ─────────────────────────────────────────────────────────────
def check_bonds() -> None:
    scr = load("bonds/screener.json")
    if scr is None:
        warn("bonds/screener.json отсутствует — пропуск bonds")
        return
    bonds = scr.get("bonds") or []
    if not bonds:
        err("bonds/screener.json: bonds пуст"); return
    meta = scr.get("meta") or {}
    if not (meta.get("updated") or meta.get("generated_at")):
        err("bonds/screener.json: нет updated/generated_at в meta")
    if not (meta.get("data_date") or meta.get("market_data_date")):
        warn("bonds/screener.json: нет data_date/market_data_date в meta")
    secids = {b.get("secid") for b in bonds}
    bad_rng = 0
    for b in bonds:
        for k, lo, hi in (("ytm_market", 0, 60), ("duration_years", 0, 30), ("price_market", 1, 300)):
            v = b.get(k)
            if isinstance(v, (int, float)) and not (lo <= v <= hi):
                bad_rng += 1
        if b.get("rating_source") != "official_issue":
            err(f"bonds/screener.json: {b.get('secid')} — рейтинг не из official_issue")
        if b.get("rating_agency") not in OFFICIAL_RATING_AGENCIES:
            err(f"bonds/screener.json: {b.get('secid')} — неизвестное рейтинговое агентство")
        host = (urlparse(str(b.get("rating_source_url") or "")).hostname or "").lower()
        if host not in OFFICIAL_RATING_HOSTS:
            err(f"bonds/screener.json: {b.get('secid')} — нет официальной ссылки рейтинга")
        if not b.get("rating_date"):
            err(f"bonds/screener.json: {b.get('secid')} — нет даты рейтингового действия")
    if bad_rng:
        err(f"bonds/screener.json: {bad_rng} значений YTM/duration/price вне разумных диапазонов")

    chart = load("bonds/chart_data.json")
    if chart is not None:
        cp = chart.get("corp_points") or []
        orphan = [c.get("secid") for c in cp if c.get("secid") not in secids]
        if orphan:
            warn(f"bonds/chart_data.json: {len(orphan)} точек не из скринера (напр. {orphan[:3]})")
    ports = load("bonds/portfolios.json")
    if ports is not None:
        p = ports.get("portfolios") or {}
        for name, port in p.items():
            if port is not None and port.get("bonds"):
                s = sum(b.get("weight", 0) for b in port["bonds"])
                if abs(s - 1.0) > 0.02:
                    warn(f"bonds/portfolios.json: '{name}' сумма весов {s:.3f} ≠ 1.0")
    fnd = load("bonds/finder.json")
    if fnd is not None:
        fm = fnd.get("meta") or {}
        if not isinstance(fm.get("warnings"), list):
            err("finder.json: meta.warnings не список (глушится слой качества данных)")
        profs = fnd.get("profiles") or {}
        if not profs:
            err("finder.json: нет profiles")
        allowed_rt = {"AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-",
                      "BB+", "BB", "BB-", "B+", "B", "B-", "CCC+", "CCC", "CCC-", "CC", "C", None}
        for pid, p in profs.items():
            picks = p.get("picks") or []
            ws = sum(x.get("weight", 0) for x in picks)
            if picks and abs(ws - 1.0) > 0.02:
                err(f"finder.json: профиль {pid} — сумма весов {ws:.3f} ≠ 1")
            for x in picks:
                if x.get("ytm") is not None and not (0 < x["ytm"] < 100):
                    err(f"finder.json: {pid}/{x.get('secid')} — абсурдный ytm {x['ytm']}")
                if x.get("rating") not in allowed_rt:
                    err(f"finder.json: {pid}/{x.get('secid')} — неизвестный рейтинг {x.get('rating')}")
                if (p.get("params") or {}).get("min_rating") and x.get("rating") is None:
                    err(f"finder.json: профиль {pid} с min_rating содержит бумагу без рейтинга {x.get('secid')}")
                if x.get("rating") is not None:
                    if x.get("rating_source") != "official_issue":
                        err(f"finder.json: {pid}/{x.get('secid')} — рейтинг не из official_issue")
                    if x.get("rating_agency") not in OFFICIAL_RATING_AGENCIES:
                        err(f"finder.json: {pid}/{x.get('secid')} — неизвестное рейтинговое агентство")
                    host = (urlparse(str(x.get("rating_source_url") or "")).hostname or "").lower()
                    if host not in OFFICIAL_RATING_HOSTS:
                        err(f"finder.json: {pid}/{x.get('secid')} — нет официальной ссылки рейтинга")
        print(f"  finder.json: профили {[ (k, len((v or {}).get('picks', []))) for k, v in profs.items() ]}")
    print(f"  bonds: {len(bonds)} бумаг, data_date={meta.get('data_date')}")


def check_news() -> None:
    d = load("news.json")
    if d is None:
        if CURRENT_SEL == "news":
            err("news.json отсутствует")
        else:
            warn("news.json отсутствует — пропуск news")
        return

    required = {
        "date": str,
        "generated_at": str,
        "session_open": str,
        "external_backdrop": str,
        "market_snapshot": list,
        "overnight": list,
        "yesterday": list,
        "today_agenda": list,
    }
    for key, typ in required.items():
        if not isinstance(d.get(key), typ):
            err(f"news.json: {key} должен быть {typ.__name__}")

    if as_date(d.get("date")) is None:
        err(f"news.json: date не ISO date: {d.get('date')!r}")
    if as_datetime(d.get("generated_at")) is None:
        err(f"news.json: generated_at не ISO datetime: {d.get('generated_at')!r}")

    market_groups = {"global_equity", "commodity", "fx", "ru_equity"}
    for idx, row in enumerate(d.get("market_snapshot") or []):
        if not isinstance(row, dict):
            err(f"news.json: market_snapshot[{idx}] не object")
            continue
        for key in ("name", "value", "change_pct", "as_of", "group"):
            if not isinstance(row.get(key), str):
                err(f"news.json: market_snapshot[{idx}].{key} должен быть string")
        if row.get("group") not in market_groups:
            err(f"news.json: market_snapshot[{idx}].group неизвестен: {row.get('group')}")

    categories = {"cb_policy", "banks", "markets", "macro", "corporate", "tech", "geopolitics"}
    for section in ("overnight", "yesterday"):
        for idx, item in enumerate(d.get(section) or []):
            if not isinstance(item, dict):
                err(f"news.json: {section}[{idx}] не object")
                continue
            for key in ("id", "headline", "context", "category", "published_at"):
                if not isinstance(item.get(key), str):
                    err(f"news.json: {section}[{idx}].{key} должен быть string")
            if item.get("category") not in categories:
                err(f"news.json: {section}[{idx}].category неизвестна: {item.get('category')}")
            if not isinstance(item.get("investment_relevant"), bool):
                err(f"news.json: {section}[{idx}].investment_relevant должен быть bool")
            imp = item.get("importance")
            if not isinstance(imp, int) or not (1 <= imp <= 5):
                err(f"news.json: {section}[{idx}].importance должен быть int 1..5")
            if as_datetime(item.get("published_at")) is None:
                err(f"news.json: {section}[{idx}].published_at не ISO datetime")
            sources = item.get("sources")
            if not isinstance(sources, list) or not sources:
                err(f"news.json: {section}[{idx}].sources пуст/не список")
            else:
                for sidx, src in enumerate(sources):
                    if not isinstance(src, dict):
                        err(f"news.json: {section}[{idx}].sources[{sidx}] не object")
                        continue
                    if not isinstance(src.get("name"), str) or not isinstance(src.get("url"), str):
                        err(f"news.json: {section}[{idx}].sources[{sidx}] без name/url string")

    agenda_types = {"dividend_cutoff", "earnings", "ofz_auction", "cb_minfin", "macro"}
    for idx, item in enumerate(d.get("today_agenda") or []):
        if not isinstance(item, dict):
            err(f"news.json: today_agenda[{idx}] не object")
            continue
        for key in ("time", "event", "ticker", "type"):
            if not isinstance(item.get(key), str):
                err(f"news.json: today_agenda[{idx}].{key} должен быть string")
        if item.get("type") not in agenda_types:
            err(f"news.json: today_agenda[{idx}].type неизвестен: {item.get('type')}")
        imp = item.get("importance")
        if not isinstance(imp, int) or not (1 <= imp <= 5):
            err(f"news.json: today_agenda[{idx}].importance должен быть int 1..5")

    print(
        f"  news.json: snapshot={len(d.get('market_snapshot') or [])}, "
        f"overnight={len(d.get('overnight') or [])}, "
        f"yesterday={len(d.get('yesterday') or [])}, agenda={len(d.get('today_agenda') or [])}"
    )


CHECKS = {"data": [check_data, check_returns], "marketsaw": [check_marketsaw],
          "market_history": [check_market_history],
          "marlamov": [check_marlamov], "quality": [check_quality],
          "bonds": [check_bonds], "news": [check_news]}


def main() -> int:
    global CURRENT_SEL
    sel = sys.argv[1] if len(sys.argv) > 1 else "all"
    CURRENT_SEL = sel
    groups = CHECKS if sel == "all" else {sel: CHECKS.get(sel, [])}
    print(f"=== validate_site_data: {sel} ===")
    for fns in groups.values():
        for fn in fns:
            fn()
    for w in WARNINGS:
        print(f"  WARNING: {w}")
    if ERRORS:
        for e in ERRORS:
            print(f"  ERROR: {e}")
        print(f"ВАЛИДАЦИЯ ПРОВАЛЕНА: {len(ERRORS)} критич. ошибок, {len(WARNINGS)} предупреждений")
        return 1
    print(f"ВАЛИДАЦИЯ OK ({len(WARNINGS)} предупреждений)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
