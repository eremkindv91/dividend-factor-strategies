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
from datetime import date

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(REPO, "site")

ERRORS: list[str] = []
WARNINGS: list[str] = []


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
    print(f"  marlamov.json: строк={len(rows)}, RFR={rfr}, режим={meta.get('regime')}")


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
        for pid, p in profs.items():
            picks = p.get("picks") or []
            ws = sum(x.get("weight", 0) for x in picks)
            if picks and abs(ws - 1.0) > 0.02:
                err(f"finder.json: профиль {pid} — сумма весов {ws:.3f} ≠ 1")
            for x in picks:
                if x.get("ytm") is not None and not (0 < x["ytm"] < 100):
                    err(f"finder.json: {pid}/{x.get('secid')} — абсурдный ytm {x['ytm']}")
        print(f"  finder.json: профили {[ (k, len((v or {}).get('picks', []))) for k, v in profs.items() ]}")
    print(f"  bonds: {len(bonds)} бумаг, data_date={meta.get('data_date')}")


CHECKS = {"data": [check_data, check_returns], "marketsaw": [check_marketsaw],
          "marlamov": [check_marlamov], "bonds": [check_bonds]}


def main() -> int:
    sel = sys.argv[1] if len(sys.argv) > 1 else "all"
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
