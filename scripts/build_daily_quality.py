#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Daily Market Data Foundation — Фаза 4: quality + coverage artifact.

Считает диагностику качества дневных рядов (per-security + агрегат покрытия) → небольшой
site/daily_market_data_quality.json. Артефакт ТОЛЬКО для диагностики и будущего Risk Engine,
основной UI не перегружает. Ряды инъектируются → тесты детерминированы, без сети/parquet.

quality_status: good | usable_with_warning | insufficient_history |
                corporate_action_unresolved | stale | unavailable.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone
from statistics import median

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daily_config as cfg  # noqa: E402
import daily_returns as dr  # noqa: E402
import trading_calendar as tc  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MASTER = os.path.join(REPO, "data", "security_master.json")
DAILY_DIR = os.path.join(REPO, "data", "daily")
OUT = os.path.join(REPO, "site", "daily_market_data_quality.json")
SCHEMA_VERSION = 1
STALE_SESSIONS = 5           # ≥ пропущенных торговых сессий после last_date → stale


def _expected_sessions(first: date, last: date) -> int:
    n, d = 0, first
    while d <= last:
        if tc.is_trading_day(d):
            n += 1
        d = date.fromordinal(d.toordinal() + 1)
    return n


def quality_one(secid: str, rows: list[dict], today: date, splits: dict | None = None,
                market_dates: set | None = None) -> dict:
    splits = splits or {}
    if not rows:
        return {"secid": secid, "observations": 0, "quality_status": "unavailable",
                "first_date": None, "last_date": None, "coverage_ratio": None,
                "reasons": ["данные отсутствуют"]}
    dates = [r["trade_date"] for r in rows]
    closes = [r.get("close") for r in rows]
    first, last = date.fromisoformat(dates[0]), date.fromisoformat(dates[-1])
    obs = len(rows)
    # expected_sessions — по ФАКТИЧЕСКОМУ рыночному календарю (включая сессии выходного дня MOEX),
    # выведенному из данных универсума; фолбэк на будни, если календарь не передан (изолированные тесты).
    if market_dates is not None:
        lo, hi = dates[0], dates[-1]
        expected = sum(1 for d in market_dates if lo <= d <= hi)
    else:
        expected = _expected_sessions(first, last)
    missing = max(0, expected - obs)
    dup = len(dates) - len(set(dates))
    nonpos = sum(1 for c in closes if not (isinstance(c, (int, float)) and c and c > 0))
    rets = [r for r in dr.simple_returns(closes) if r is not None]
    extreme = sum(1 for r in rets if abs(r) > cfg.EXTREME_DAILY_RETURN)
    suspected = sum(1 for r_i, r in enumerate(dr.simple_returns(closes))
                    if r is not None and abs(r) > cfg.EXTREME_DAILY_RETURN and dates[r_i + 1] not in splits)
    confirmed = len(splits)
    stale = tc.sessions_between(last, tc.to_msk(datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)))

    reasons = []
    status = "good"
    if stale >= STALE_SESSIONS:
        status, _ = "stale", reasons.append("ряд устарел")
    elif suspected > 0:
        status, _ = "corporate_action_unresolved", reasons.append("нераспознанное корпоративное действие")
    elif obs < cfg.HISTORY_INSUFFICIENT:
        status, _ = "insufficient_history", reasons.append("недостаточная история")
    elif (expected and obs / expected < 0.98) or nonpos > 0 or dup > 0:
        status, _ = "usable_with_warning", reasons.append("неполное покрытие/аномалии — пригодно с оговоркой")
    return {
        "secid": secid,
        "first_date": dates[0], "last_date": dates[-1],
        "observations": obs, "expected_sessions": expected,
        "coverage_ratio": round(obs / expected, 4) if expected else None,
        "missing_sessions": missing, "duplicate_dates": dup, "non_positive_prices": nonpos,
        "extreme_returns": extreme, "suspected_corporate_actions": suspected,
        "confirmed_corporate_actions": confirmed, "stale_sessions": stale,
        "quality_status": status, "reasons": reasons,
    }


def compute(series_map: dict[str, list[dict]], today: date, splits_map: dict | None = None,
            universe_size: int | None = None) -> dict:
    splits_map = splits_map or {}
    # рыночный календарь = объединение всех фактических торговых дат универсума (учитывает
    # сессии выходного дня MOEX и праздники автоматически, без предположений о буднях)
    market_dates = {r["trade_date"] for rows in series_map.values() for r in rows}
    per = [quality_one(s, rows, today, splits_map.get(s), market_dates)
           for s, rows in sorted(series_map.items())]

    def _is(st):
        return [p for p in per if p["quality_status"] == st]
    usable = [p for p in per if p["quality_status"] in ("good", "usable_with_warning")
              and p["observations"] >= cfg.HISTORY_INSUFFICIENT]
    excluded = [p for p in per if p["quality_status"] in ("unavailable", "stale", "insufficient_history")]
    lengths = sorted(p["observations"] for p in per if p["observations"] > 0)
    reasons_hist = {}
    for p in excluded:
        for r in p["reasons"]:
            reasons_hist[r] = reasons_hist.get(r, 0) + 1
    dist = {"good": len(_is("good")), "usable_with_warning": len(_is("usable_with_warning")),
            "insufficient_history": len(_is("insufficient_history")),
            "corporate_action_unresolved": len(_is("corporate_action_unresolved")),
            "stale": len(_is("stale")), "unavailable": len(_is("unavailable"))}
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_as_of": max((p["last_date"] for p in per if p["last_date"]), default=None),
        "coverage": {
            "universe_size": universe_size if universe_size is not None else len(per),
            "available": sum(1 for p in per if p["observations"] > 0),
            "usable_for_risk": len(usable),
            "with_warnings": len(_is("usable_with_warning")) + len(_is("corporate_action_unresolved")),
            "excluded": len(excluded),
            "exclusion_reasons": reasons_hist,
            "history_len_min": lengths[0] if lengths else 0,
            "history_len_median": int(median(lengths)) if lengths else 0,
            "history_len_max": lengths[-1] if lengths else 0,
        },
        "status_distribution": dist,
        "unresolved_corporate_actions": [
            {"secid": p["secid"], "suspected": p["suspected_corporate_actions"]}
            for p in per if p["quality_status"] == "corporate_action_unresolved"],
        "securities": per,
    }


def validate(payload: dict) -> list[str]:
    """Контракт-валидатор daily_market_data_quality.json. [] = валиден."""
    errs = []
    if not isinstance(payload, dict):
        return ["корень не объект"]
    if payload.get("schema_version") != SCHEMA_VERSION:
        errs.append(f"schema_version != {SCHEMA_VERSION}")
    if "coverage" not in payload:
        errs.append("нет coverage")
    secs = payload.get("securities")
    if not isinstance(secs, list):
        errs.append("securities не список")
        return errs
    valid_status = {"good", "usable_with_warning", "insufficient_history",
                    "corporate_action_unresolved", "stale", "unavailable"}
    for p in secs:
        if p.get("quality_status") not in valid_status:
            errs.append(f"{p.get('secid')}: недопустимый quality_status {p.get('quality_status')}")
        cr = p.get("coverage_ratio")
        if cr is not None and not (0 <= cr <= 1.0001):
            errs.append(f"{p.get('secid')}: coverage_ratio вне [0,1] ({cr})")
    return errs


def _load_series_from_daily(daily_dir: str) -> dict[str, list[dict]]:
    import daily_store as store
    man = store.load_manifest(os.path.join(daily_dir, "manifest.json"))
    out = {}
    for secid in man.get("series", {}):
        out[secid] = store.read_parquet(os.path.join(daily_dir, "prices", f"{secid}.parquet"))
    return out


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else OUT
    series = _load_series_from_daily(DAILY_DIR)
    today = tc.to_msk(datetime.now(timezone.utc)).date()
    payload = compute(series, today)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, out)
    c = payload["coverage"]
    sys.stderr.write(f"[daily-quality] universe={c['universe_size']} usable_for_risk={c['usable_for_risk']} "
                     f"excluded={c['excluded']} → {out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
