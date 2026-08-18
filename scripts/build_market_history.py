#!/usr/bin/env python3
"""Build daily MOEX market charts and transparent technical reference levels."""
from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests
from ta.momentum import RSIIndicator
from ta.trend import SMAIndicator
from ta.volatility import AverageTrueRange


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "site" / "market_history.json"
ISS = "https://iss.moex.com/iss"
HEADERS = {"User-Agent": "dividend-factor-strategies/market-history (+https://github.com/eremkindv91/dividend-factor-strategies)"}
PAGE_SIZE = 100
YEARS = 6
SCHEMA = 2

INSTRUMENTS = [
    {"id": "MCFTR", "name": "MCFTR", "description": "Индекс полной доходности МосБиржи", "engine": "stock", "market": "index", "board": "RTSI", "decimals": 2},
    {"id": "IMOEX", "name": "IMOEX", "description": "Индекс МосБиржи", "engine": "stock", "market": "index", "board": "SNDX", "decimals": 2},
    {"id": "RTSI", "name": "RTS", "description": "Индекс РТС", "engine": "stock", "market": "index", "board": "RTSI", "decimals": 2},
    {"id": "USD000UTSTOM", "name": "USD/RUB", "description": "Доллар США к рублю, расчёты tomorrow", "engine": "currency", "market": "selt", "board": "CETS", "decimals": 4},
    {"id": "CNYRUB_TOM", "name": "CNY/RUB", "description": "Китайский юань к рублю, расчёты tomorrow", "engine": "currency", "market": "selt", "board": "CETS", "decimals": 4},
]


def log(message: str) -> None:
    print(f"[market-history] {message}", file=sys.stderr)


def _get_json(session: requests.Session, url: str, params: dict) -> dict:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = session.get(url, params=params, timeout=25)
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < 2:
                time.sleep(2**attempt)
    raise RuntimeError(f"MOEX ISS unavailable: {last_error}")


def history_url(spec: dict) -> str:
    return (
        f"{ISS}/history/engines/{spec['engine']}/markets/{spec['market']}"
        f"/boards/{spec['board']}/securities/{spec['id']}.json"
    )


def candles_url(spec: dict) -> str:
    return (
        f"{ISS}/engines/{spec['engine']}/markets/{spec['market']}"
        f"/boards/{spec['board']}/securities/{spec['id']}/candles.json"
    )


def aggregate_current_session(payload: dict) -> dict | None:
    """Aggregate only real MOEX 10-minute candles from the latest session."""
    block = payload.get("candles") or {}
    columns = block.get("columns") or []
    rows = []
    for values in block.get("data") or []:
        item = dict(zip(columns, values))
        try:
            row = {
                "date": str(item["begin"])[:10],
                "begin": str(item["begin"]),
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
            }
        except (KeyError, TypeError, ValueError):
            continue
        if min(row["open"], row["high"], row["low"], row["close"]) <= 0:
            continue
        if not (row["low"] <= min(row["open"], row["close"])
                <= max(row["open"], row["close"]) <= row["high"]):
            continue
        rows.append(row)
    if not rows:
        return None
    latest_date = max(row["date"] for row in rows)
    session = sorted((row for row in rows if row["date"] == latest_date), key=lambda row: row["begin"])
    return {
        "date": latest_date,
        "open": session[0]["open"],
        "high": max(row["high"] for row in session),
        "low": min(row["low"] for row in session),
        "close": session[-1]["close"],
        "last_candle_at": session[-1]["begin"],
        "candle_count": len(session),
        "status": "current_session",
    }


def fetch_current_session(spec: dict, from_date: str,
                          session: requests.Session | None = None) -> dict | None:
    session = session or requests.Session()
    session.headers.update(HEADERS)
    payload = _get_json(
        session,
        candles_url(spec),
        {
            "iss.meta": "off",
            "iss.only": "candles",
            "interval": 10,
            "from": from_date,
            "candles.columns": "begin,open,high,low,close",
        },
    )
    return aggregate_current_session(payload)


def fetch_history(spec: dict, from_date: str, session: requests.Session | None = None) -> list[dict]:
    session = session or requests.Session()
    session.headers.update(HEADERS)
    rows: list[dict] = []
    start = 0
    while True:
        payload = _get_json(
            session,
            history_url(spec),
            {
                "iss.meta": "off",
                "from": from_date,
                "start": start,
                "history.columns": "TRADEDATE,OPEN,HIGH,LOW,CLOSE,VALUE",
            },
        )
        block = payload.get("history") or {}
        columns = block.get("columns") or []
        page = block.get("data") or []
        for values in page:
            item = dict(zip(columns, values))
            try:
                row = {
                    "date": str(item["TRADEDATE"]),
                    "open": float(item["OPEN"]),
                    "high": float(item["HIGH"]),
                    "low": float(item["LOW"]),
                    "close": float(item["CLOSE"]),
                }
            except (KeyError, TypeError, ValueError):
                continue
            if min(row["open"], row["high"], row["low"], row["close"]) <= 0:
                continue
            if not (row["low"] <= min(row["open"], row["close"]) <= max(row["open"], row["close"]) <= row["high"]):
                continue
            rows.append(row)
        if len(page) < PAGE_SIZE:
            break
        start += PAGE_SIZE
        time.sleep(0.05)
    deduped = {row["date"]: row for row in rows}
    result = [deduped[key] for key in sorted(deduped)]
    if len(result) < 220:
        raise RuntimeError(f"{spec['id']}: only {len(result)} valid OHLC rows")
    return result


def _round(value, digits: int):
    return None if value is None or pd.isna(value) else round(float(value), digits)


def calculate_instrument(spec: dict, rows: list[dict], checked_at: str) -> dict:
    decimals = int(spec["decimals"])
    frame = pd.DataFrame(rows).sort_values("date").drop_duplicates("date", keep="last")
    close = frame["close"].astype(float)
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    for window in (20, 50, 200):
        frame[f"sma{window}"] = SMAIndicator(close=close, window=window, fillna=False).sma_indicator()
    frame["rsi14"] = RSIIndicator(close=close, window=14, fillna=False).rsi()
    frame["atr14"] = AverageTrueRange(high=high, low=low, close=close, window=14, fillna=False).average_true_range()
    frame["vol20"] = close.pct_change().rolling(20).std() * math.sqrt(252) * 100

    latest = frame.iloc[-1]
    previous = frame.iloc[-2]
    last = float(latest["close"])
    change_pct = (last / float(previous["close"]) - 1) * 100
    sma20 = _round(latest["sma20"], decimals)
    sma50 = _round(latest["sma50"], decimals)
    sma200 = _round(latest["sma200"], decimals)
    if sma200 is None:
        trend = "Недостаточно истории для SMA200"
    elif last > sma200 and sma20 is not None and sma50 is not None and sma20 > sma50:
        trend = "Выше SMA200; SMA20 выше SMA50"
    elif last < sma200 and sma20 is not None and sma50 is not None and sma20 < sma50:
        trend = "Ниже SMA200; SMA20 ниже SMA50"
    else:
        trend = "Смешанная структура средних"

    def range_values(window: int) -> tuple[float, float]:
        tail = frame.tail(window)
        return float(tail["low"].min()), float(tail["high"].max())

    low20, high20 = range_values(20)
    low60, high60 = range_values(60)
    low252, high252 = range_values(252)
    columns = ["date", "open", "high", "low", "close", "sma20", "sma50", "sma200"]
    series = [
        [
            row["date"],
            _round(row["open"], decimals),
            _round(row["high"], decimals),
            _round(row["low"], decimals),
            _round(row["close"], decimals),
            _round(row["sma20"], decimals),
            _round(row["sma50"], decimals),
            _round(row["sma200"], decimals),
        ]
        for _, row in frame.iterrows()
    ]
    return {
        "id": spec["id"],
        "name": spec["name"],
        "description": spec["description"],
        "decimals": decimals,
        "data_last": str(latest["date"]),
        "checked_at": checked_at,
        "source": "MOEX ISS",
        "source_url": history_url(spec),
        "columns": columns,
        "series": series,
        "summary": {
            "last": round(last, decimals),
            "change_pct": round(change_pct, 4),
            "sma20": sma20,
            "sma50": sma50,
            "sma200": sma200,
            "rsi14": _round(latest["rsi14"], 1),
            "volatility20_annualized_pct": _round(latest["vol20"], 1),
            "atr14": _round(latest["atr14"], decimals),
            "low20": round(low20, decimals),
            "high20": round(high20, decimals),
            "low60": round(low60, decimals),
            "high60": round(high60, decimals),
            "low252": round(low252, decimals),
            "high252": round(high252, decimals),
            "trend": trend,
        },
    }


def load_previous(path: Path) -> dict[str, dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {row["id"]: row for row in payload.get("instruments") or [] if isinstance(row, dict) and row.get("id")}
    except (OSError, ValueError, TypeError):
        return {}


def build(output: Path = DEFAULT_OUT, today: date | None = None) -> dict:
    today = today or date.today()
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    from_date = (today - timedelta(days=YEARS * 366)).isoformat()
    previous = load_previous(output)
    session = requests.Session()
    session.headers.update(HEADERS)
    instruments = []
    errors = []
    intraday_errors = []
    for spec in INSTRUMENTS:
        try:
            rows = fetch_history(spec, from_date, session=session)
            instrument = calculate_instrument(spec, rows, checked_at)
            try:
                current = fetch_current_session(
                    spec, (today - timedelta(days=7)).isoformat(), session=session,
                )
                if current and current["date"] > instrument["data_last"]:
                    current.update({
                        "source": "MOEX ISS official 10-minute candles",
                        "source_url": candles_url(spec),
                        "checked_at": checked_at,
                    })
                    instrument["current_session"] = current
                    log(f"{spec['id']}: current session through {current['last_candle_at']}")
            except Exception as exc:  # noqa: BLE001 - optional live snapshot
                intraday_errors.append({"instrument": spec["id"], "error": str(exc)[:180]})
                log(f"{spec['id']}: current session unavailable: {exc}")
            instruments.append(instrument)
            log(f"{spec['id']}: {len(rows)} rows through {rows[-1]['date']}")
        except Exception as exc:  # noqa: BLE001 - preserve per-instrument last good
            old = previous.get(spec["id"])
            errors.append({"instrument": spec["id"], "error": str(exc)[:180], "fallback": bool(old)})
            if old:
                old = dict(old)
                old["fallback"] = True
                instruments.append(old)
            log(f"{spec['id']}: {exc}")
    ids = {row["id"] for row in instruments}
    if not {"MCFTR", "IMOEX", "RTSI"}.issubset(ids):
        raise RuntimeError("core MOEX index history is incomplete")
    # source_as_of = фактическая последняя дата ряда (max TRADEDATE по инструментам),
    # НЕ время экспорта JSON. Read-слой (build_site_status) читает именно это поле.
    data_dates = [str(row.get("data_last")) for row in instruments if row.get("data_last")]
    data_asof = max(data_dates) if data_dates else None
    current_dates = [str((row.get("current_session") or {}).get("date"))
                     for row in instruments if (row.get("current_session") or {}).get("date")]
    return {
        "schema": SCHEMA,
        "generated_at": checked_at,
        "data_asof": data_asof,
        "source": "MOEX ISS official daily history",
        "history_from": from_date,
        "methodology": "SMA and RSI use the ta library; ranges are observed rolling OHLC extrema; volatility is 20-session close-to-close standard deviation annualized by sqrt(252).",
        "errors": errors,
        "intraday_errors": intraday_errors,
        "current_session_asof": max(current_dates) if current_dates else None,
        "instruments": instruments,
    }


def main() -> int:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    try:
        payload = build(output)
    except Exception as exc:  # noqa: BLE001
        log(f"STOP, existing file preserved: {exc}")
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, output)
    log(f"OK -> {output} ({len(payload['instruments'])} instruments, errors={len(payload['errors'])})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
