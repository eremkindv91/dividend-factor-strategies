#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Daily Risk Engine v1 — веб-мост: компактные дневные доходности для КЛИЕНТСКОГО расчёта.

Из дневного parquet Foundation + подтверждённых сплитов → adjusted price returns; из
market_history.json → бенчмарк IMOEX. Публикует ПО ФАЙЛУ НА БУМАГУ (site/daily/web/{SECID}.json),
чтобы браузер грузил только бумаги своего портфеля (приватность: состав не покидает клиента;
перф: ~4 КБ/бумага вместо мегабайтного универсума). Окно ограничено WINDOW дней.

Ряды/сплиты/бенчмарк инъектируются → тесты детерминированы, без сети/parquet.
Доходности ограничены WINDOW, а официальный OHLCV для ленивого графика — CHART_WINDOW.
"""
from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daily_corp_actions as ca  # noqa: E402
import daily_returns as dret  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAILY_DIR = os.path.join(REPO, "data", "daily")
MARKET_HISTORY = os.path.join(REPO, "site", "market_history.json")
QUALITY = os.path.join(REPO, "site", "daily_market_data_quality.json")
OUT_DIR = os.path.join(REPO, "site", "daily", "web")
SCHEMA_VERSION = 2
WINDOW = 504                # ≈2 года торговых дней (дневной риск; хвостовые — с пометкой confidence)
CHART_WINDOW = 850           # >3 лет сессий; полный ряд остаётся в parquet, браузер грузит лениво
ROUND = 6


def _returns_from_rows(rows: list[dict], splits: dict | None) -> tuple[list[str], list[float], str, str]:
    """(dates, adjusted_simple_returns, return_type, corporate_action_status)."""
    enr = ca.apply_corporate_actions(rows, splits or {})
    dates = [e["trade_date"] for e in enr["rows"]]
    adj = [e["adjusted_close"] for e in enr["rows"]]
    rets = dret.simple_returns(adj)                       # первый None отбрасываем ниже
    out_dates, out_rets = [], []
    for i, r in enumerate(rets):
        if r is not None:
            out_dates.append(dates[i + 1])
            out_rets.append(round(r, ROUND))
    ca_status = "unresolved" if enr["summary"]["suspected_corporate_actions"] else "resolved"
    rtype = "adjusted_price_return" if splits else "adjusted_price_return"
    return out_dates, out_rets, rtype, ca_status


def _finite(value):
    return value if isinstance(value, (int, float)) and math.isfinite(value) else None


def _ohlcv_from_rows(rows: list[dict], window: int = CHART_WINDOW) -> list[list]:
    """Компактный официальный OHLCV для графика; неполные свечи не синтезируем."""
    out = []
    for row in rows[-window:]:
        values = [_finite(row.get(key)) for key in ("open", "high", "low", "close")]
        date_value = str(row.get("trade_date") or "")[:10]
        if len(date_value) != 10 or any(value is None for value in values):
            continue
        out.append([
            date_value,
            *values,
            _finite(row.get("volume")),
            _finite(row.get("value")),
        ])
    return out


def build_one(secid: str, rows: list[dict], splits: dict | None, quality_status: str,
              window: int = WINDOW, chart_window: int = CHART_WINDOW) -> dict | None:
    if not rows or len(rows) < 2:
        return None
    dates, rets, rtype, ca_status = _returns_from_rows(rows, splits)
    if not rets:
        return None
    dates, rets = dates[-window:], rets[-window:]
    ohlcv = _ohlcv_from_rows(rows, chart_window)
    return {
        "schema_version": SCHEMA_VERSION, "secid": secid, "return_type": rtype,
        "quality_status": quality_status, "corporate_action_status": ca_status,
        "fallback_status": "none", "first_date": dates[0], "last_date": dates[-1],
        "observations": len(rets), "source_as_of": dates[-1],
        "dates": dates, "returns": rets,
        "ohlcv_observations": len(ohlcv), "ohlcv": ohlcv,
    }


def build_benchmark(mh_instruments: list[dict], ticker: str = "IMOEX", window: int = WINDOW) -> dict | None:
    """Бенчмарк из market_history.json: серия индекса → простые доходности. IMOEX = price index."""
    inst = next((i for i in mh_instruments if i.get("id") == ticker), None)
    if not inst or not inst.get("series"):
        return None
    ser = inst["series"]                                  # [[date, open, high, low, close, ...], ...] или [[date, close]]
    dates = [str(row[0])[:10] for row in ser]
    closes = [float(row[4]) if len(row) > 4 else float(row[1]) for row in ser]
    rets = dret.simple_returns(closes)
    od, orr = [], []
    for i, r in enumerate(rets):
        if r is not None:
            od.append(dates[i + 1]); orr.append(round(r, ROUND))
    od, orr = od[-window:], orr[-window:]
    return {"schema_version": SCHEMA_VERSION, "ticker": ticker, "type": "price_index",
            "source_as_of": od[-1] if od else None, "dates": od, "returns": orr}


def build(series_map: dict[str, list[dict]], splits_map: dict, quality_map: dict,
          mh_instruments: list[dict], out_dir: str, window: int = WINDOW,
          alias_map: dict | None = None) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    index = {"schema_version": SCHEMA_VERSION,
             "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "window": window, "benchmark": None,
             "aliases": alias_map or {}, "securities": {}}

    bench = build_benchmark(mh_instruments, window=window)
    if bench:
        _atomic(os.path.join(out_dir, "_benchmark.json"), bench)
        index["benchmark"] = {"ticker": bench["ticker"], "type": bench["type"],
                              "source_as_of": bench["source_as_of"], "observations": len(bench["returns"])}

    for secid in sorted(series_map):
        q = quality_map.get(secid, "unavailable")
        if q in ("unavailable", "stale"):                 # недоступные/устаревшие не публикуем для риска
            index["securities"][secid] = {"published": False, "quality_status": q}
            continue
        rec = build_one(secid, series_map[secid], splits_map.get(secid), q, window)
        if rec is None:
            index["securities"][secid] = {"published": False, "quality_status": q}
            continue
        _atomic(os.path.join(out_dir, f"{secid}.json"), rec)
        index["securities"][secid] = {"published": True, "quality_status": q,
                                      "return_type": rec["return_type"],
                                      "corporate_action_status": rec["corporate_action_status"],
                                      "observations": rec["observations"], "first_date": rec["first_date"]}
    _atomic(os.path.join(out_dir, "_index.json"), index)
    return index


def _atomic(path: str, obj: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def main() -> int:
    import daily_store as store
    man = store.load_manifest(os.path.join(DAILY_DIR, "manifest.json"))
    series_map, quality_map = {}, {}
    for secid, meta in man.get("series", {}).items():
        rows = store.read_parquet(os.path.join(DAILY_DIR, "prices", f"{secid}.parquet"))
        if rows:
            series_map[secid] = rows
    try:
        q = json.load(open(QUALITY, encoding="utf-8"))
        quality_map = {s["secid"]: s["quality_status"] for s in q.get("securities", [])}
    except (OSError, ValueError):
        quality_map = {s: "good" for s in series_map}
    splits_map = {s: ca.fetch_splits_iss(s) for s in series_map}
    try:
        mh = json.load(open(MARKET_HISTORY, encoding="utf-8")).get("instruments", [])
    except (OSError, ValueError):
        mh = []
    alias_map = {}
    try:
        master = json.load(open(os.path.join(REPO, "data", "security_master.json"), encoding="utf-8"))
        for s in master.get("securities", []):
            for prev in s.get("previous_tickers", []):
                alias_map[prev] = s["canonical_ticker"]
    except (OSError, ValueError):
        pass
    idx = build(series_map, splits_map, quality_map, mh, OUT_DIR, alias_map=alias_map)
    pub = sum(1 for v in idx["securities"].values() if v.get("published"))
    sys.stderr.write(f"[daily-web] опубликовано {pub} бумаг + бенчмарк → {OUT_DIR}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
