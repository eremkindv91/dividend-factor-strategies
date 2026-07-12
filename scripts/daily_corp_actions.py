#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Daily Market Data Foundation — Фаза 3: явная обработка корпоративных действий.

Политика (quant-honesty):
  • сплиты применяются ТОЛЬКО из подтверждённого источника (ISS splits API), back-adjust;
  • эвристически найденный разрыв цены → статус SUSPECTED (НЕ применяется молча);
  • дивиденд — отдельный денежный поток, НЕ сплит;
  • изменение ряда воспроизводимо; число затронутых наблюдений логируется;
  • неожиданный масштаб (|фактор| > SPLIT_SANE_MAX) БЛОКИРУЕТ пайплайн (raise).

Не хардкодить методологию под конкретные тикеры — TRNFP/PLZL покрыты regression-тестами.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daily_config as cfg  # noqa: E402
import daily_returns as dr  # noqa: E402


class CorporateActionError(RuntimeError):
    """Аномальный масштаб корректировки — пайплайн должен остановиться, не искажать ряд."""


def cumulative_adjustment(dates: list[str], splits: dict[str, float]) -> list[float]:
    """adjustment_factor[t] = произведение сплит-факторов с ex-датой СТРОГО ПОЗЖЕ t.
    Делает ряд непрерывным (back-adjust): цены до сплита делятся на будущий фактор."""
    for sf in splits.values():
        if abs(sf) > cfg.SPLIT_SANE_MAX:
            raise CorporateActionError(f"сплит-фактор {sf} > {cfg.SPLIT_SANE_MAX} — аномалия")
    out = []
    items = list(splits.items())
    for d in dates:
        f = 1.0
        for sd, sfac in items:
            if sd > d:
                f *= sfac
        out.append(f)
    return out


def detect_suspected(rows: list[dict], splits: dict[str, float]) -> list[dict]:
    """Экстремальная дневная доходность без подтверждённого сплита на дату → SUSPECTED (не применяем)."""
    closes = [r.get("close") for r in rows]
    rets = dr.simple_returns(closes)
    out = []
    for i, r in enumerate(rets):
        if r is not None and abs(r) > cfg.EXTREME_DAILY_RETURN:
            d = rows[i + 1]["trade_date"]
            if d not in splits:
                out.append({"date": d, "return": round(r, 4), "status": "suspected",
                            "note": "экстремальная доходность без подтверждённого сплита — требует проверки"})
    return out


def apply_corporate_actions(rows: list[dict], splits: dict[str, float] | None = None,
                            dividends: dict[str, float] | None = None) -> dict:
    """Обогатить raw-ряд до контракта дневного слоя. Возвращает {rows, summary}.

    Контракт строки: trade_date, raw_close, adjusted_close, adjustment_factor, split_factor,
    dividend_cash, total_return_index (если дивиденды подтверждены, иначе None).
    """
    splits = splits or {}
    dividends = dividends or {}
    dates = [r["trade_date"] for r in rows]
    adj = cumulative_adjustment(dates, splits)

    enriched, obs_adjusted = [], 0
    for i, r in enumerate(rows):
        raw = r.get("close")
        factor = adj[i]
        adjusted = (raw / factor) if (raw is not None and factor) else None
        if factor != 1.0:
            obs_adjusted += 1
        enriched.append({
            "trade_date": r["trade_date"],
            "raw_close": raw,
            "adjusted_close": adjusted,
            "adjustment_factor": factor,
            "split_factor": splits.get(r["trade_date"], 1.0),
            "dividend_cash": dividends.get(r["trade_date"], 0.0),
        })

    # total_return_index — только если дивиденды подтверждены (иначе методологически неполно → None)
    tri = None
    if dividends:
        adj_closes = [e["adjusted_close"] for e in enriched]
        div_series = [e["dividend_cash"] for e in enriched]
        trs = dr.total_returns(adj_closes, div_series)
        idx, val = [1.0], 1.0
        for tr in trs:
            val = val * (1.0 + tr) if tr is not None else val
            idx.append(val)
        for e, v in zip(enriched, idx):
            e["total_return_index"] = v
        tri = "confirmed"
    else:
        for e in enriched:
            e["total_return_index"] = None

    suspected = detect_suspected(rows, splits)
    summary = {
        "splits_applied": len(splits),
        "obs_adjusted": obs_adjusted,
        "dividends": len(dividends),
        "suspected_corporate_actions": suspected,
        "total_return_index": tri or "unavailable",
    }
    return {"rows": enriched, "summary": summary}


def fetch_splits_iss(secid: str, http=None) -> dict[str, float]:
    """Подтверждённые сплиты бумаги из ISS splits API → {ex_date: factor}. Injectable http для тестов."""
    if http is None:
        import moex_iss
        http = moex_iss._http_get_json  # noqa: SLF001
    url = f"https://iss.moex.com/iss/statistics/engines/stock/splits.json?iss.meta=off&securities={secid}"
    try:
        payload = http(url)
    except Exception:  # noqa: BLE001
        return {}
    blk = payload.get("splits", {"columns": [], "data": []})
    cols = blk.get("columns", [])
    out = {}
    for row in blk.get("data", []):
        rec = dict(zip(cols, row))
        if str(rec.get("secid") or rec.get("SECID") or "").upper() != secid.upper():
            continue
        d = str(rec.get("tradedate") or rec.get("TRADEDATE") or "")[:10]
        before = rec.get("before") or rec.get("BEFORE")
        after = rec.get("after") or rec.get("AFTER")
        if d and before and after and float(after) > 0:
            out[d] = float(after) / float(before)     # factor>1 при дроблении (100:1 → 100)
    return out
