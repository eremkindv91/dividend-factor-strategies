from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Callable

import pandas as pd


@dataclass
class MarketData:
    close: pd.DataFrame
    traded_value: pd.DataFrame
    volume: pd.DataFrame
    benchmark: pd.Series
    macro: dict[str, pd.Series]
    dividends: dict[str, list[dict]]
    master: dict[str, dict]
    source_rows: dict[str, int]

    @property
    def as_of(self) -> pd.Timestamp:
        return min(self.close.dropna(how="all").index.max(), self.benchmark.dropna().index.max())


def load_security_master(path: Path) -> dict[str, dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for row in payload.get("securities", []):
        secid = row.get("secid")
        if secid and row.get("instrument_type") == "share":
            out[str(secid)] = row
    return out


def _read_price(path: Path) -> pd.DataFrame:
    frame = pd.read_parquet(path)
    required = {"trade_date", "close", "value", "volume"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"{path.name}: missing columns {sorted(missing)}")
    frame = frame.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    for column in ("close", "value", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["trade_date", "close"])
    frame = frame[frame["close"] > 0].drop_duplicates("trade_date", keep="last").sort_values("trade_date")
    return frame.set_index("trade_date")[["close", "value", "volume"]]


def load_market_data(
    daily_root: Path,
    master_path: Path,
    benchmark_path: Path,
    dividends_path: Path | None = None,
    macro_paths: dict[str, Path] | None = None,
) -> MarketData:
    master = load_security_master(master_path)
    prices: dict[str, pd.DataFrame] = {}
    for path in sorted((daily_root / "prices").glob("*.parquet")):
        secid = path.stem.upper()
        if secid not in master:
            continue
        frame = _read_price(path)
        if not frame.empty:
            prices[secid] = frame
    if not prices:
        raise ValueError("no real MOEX price parquet files found")
    benchmark_frame = pd.read_parquet(benchmark_path)
    date_column = "trade_date" if "trade_date" in benchmark_frame else "date"
    close_column = "close"
    if date_column not in benchmark_frame or close_column not in benchmark_frame:
        raise ValueError("benchmark parquet requires trade_date/date and close")
    benchmark_frame[date_column] = pd.to_datetime(benchmark_frame[date_column], errors="coerce")
    benchmark_frame[close_column] = pd.to_numeric(benchmark_frame[close_column], errors="coerce")
    benchmark = (
        benchmark_frame.dropna(subset=[date_column, close_column])
        .drop_duplicates(date_column, keep="last")
        .set_index(date_column)[close_column]
        .sort_index()
    )
    if benchmark.empty:
        raise ValueError("MCFTR benchmark is empty")
    if macro_paths is None:
        macro_paths = {
            name: daily_root / "benchmarks" / f"{name}.parquet"
            for name in ("IMOEX", "RGBI", "USDRUB", "KEY_RATE")
        }
    macro: dict[str, pd.Series] = {}
    for name, path in macro_paths.items():
        if not path.exists():
            continue
        macro_frame = pd.read_parquet(path)
        macro_date = "trade_date" if "trade_date" in macro_frame else "date"
        macro_value = next((column for column in ("close", "value", "rate") if column in macro_frame), None)
        if macro_date not in macro_frame or macro_value is None:
            continue
        macro_frame[macro_date] = pd.to_datetime(macro_frame[macro_date], errors="coerce")
        macro_frame[macro_value] = pd.to_numeric(macro_frame[macro_value], errors="coerce")
        series = (
            macro_frame.dropna(subset=[macro_date, macro_value])
            .drop_duplicates(macro_date, keep="last")
            .set_index(macro_date)[macro_value]
            .sort_index()
        )
        if not series.empty:
            macro[name] = series
    close = pd.concat({ticker: frame["close"] for ticker, frame in prices.items()}, axis=1).sort_index()
    value = pd.concat({ticker: frame["value"] for ticker, frame in prices.items()}, axis=1).reindex(close.index)
    volume = pd.concat({ticker: frame["volume"] for ticker, frame in prices.items()}, axis=1).reindex(close.index)
    dividends: dict[str, list[dict]] = {}
    if dividends_path and dividends_path.exists():
        raw = json.loads(dividends_path.read_text(encoding="utf-8"))
        dividends = raw.get("securities", raw) if isinstance(raw, dict) else {}
    return MarketData(
        close=close,
        traded_value=value,
        volume=volume,
        benchmark=benchmark,
        macro=macro,
        dividends=dividends,
        master=master,
        source_rows={ticker: len(frame) for ticker, frame in prices.items()},
    )


def refresh_index_cache(
    output: Path,
    secid: str,
    fetch: Callable[[str], list[tuple[str, float]]] | None = None,
) -> Path:
    if fetch is None:
        repo = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(repo / "market_saw" / "shared"))
        from moex_index_fetch import fetch_index_history

        fetch = fetch_index_history
    rows = fetch(secid)
    if len(rows) < 252:
        raise ValueError(f"{secid}: only {len(rows)} official history rows")
    frame = pd.DataFrame(rows, columns=["trade_date", "close"]).drop_duplicates("trade_date", keep="last")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp.parquet")
    frame.to_parquet(temporary, index=False)
    temporary.replace(output)
    return output


def refresh_moex_instrument_cache(output: Path, spec: dict, from_date: str) -> Path:
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo / "scripts"))
    from build_market_history import fetch_history

    rows = fetch_history(spec, from_date)
    frame = pd.DataFrame(
        {"trade_date": [row["date"] for row in rows], "close": [row["close"] for row in rows]}
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp.parquet")
    frame.to_parquet(temporary, index=False)
    temporary.replace(output)
    return output


def refresh_cbr_key_rate_cache(output: Path, from_date: str = "01.01.2018") -> Path:
    import requests

    response = requests.get(
        "https://www.cbr.ru/hd_base/KeyRate/",
        params={
            "UniDbQuery.Posted": "True",
            "UniDbQuery.From": from_date,
            "UniDbQuery.To": date.today().strftime("%d.%m.%Y"),
        },
        headers={"User-Agent": "dividend-factor-strategies/ml-strategy"},
        timeout=30,
    )
    response.raise_for_status()
    selected = None
    for frame in pd.read_html(StringIO(response.text), decimal=",", thousands=" "):
        columns = [str(column).strip().lower() for column in frame.columns]
        if any("дата" in column for column in columns) and any("став" in column for column in columns):
            selected = frame.copy()
            selected.columns = columns
            break
    if selected is None:
        raise ValueError("CBR key-rate table not found")
    date_column = next(column for column in selected.columns if "дата" in column)
    rate_column = next(column for column in selected.columns if "став" in column)
    dates = pd.to_datetime(selected[date_column], dayfirst=True, errors="coerce")
    rates = pd.to_numeric(
        selected[rate_column].astype(str).str.replace(" ", "", regex=False).str.replace(",", ".", regex=False),
        errors="coerce",
    )
    frame = pd.DataFrame({"trade_date": dates, "rate": rates}).dropna().sort_values("trade_date")
    if len(frame) < 100:
        raise ValueError(f"CBR key-rate history too short: {len(frame)}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp.parquet")
    frame.to_parquet(temporary, index=False)
    temporary.replace(output)
    return output


def refresh_dividend_cache(
    tickers: list[str],
    output: Path,
    fetch: Callable[[str], list[dict]] | None = None,
    delay_seconds: float = 0.05,
) -> Path:
    if fetch is None:
        repo = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(repo / "scripts"))
        from moex_iss import fetch_dividend_records

        fetch = fetch_dividend_records
    previous: dict = {}
    if output.exists():
        try:
            previous = json.loads(output.read_text(encoding="utf-8")).get("securities", {})
        except (OSError, json.JSONDecodeError):
            previous = {}
    securities: dict[str, list[dict]] = dict(previous)
    errors: list[dict] = []
    for ticker in sorted(set(tickers)):
        try:
            securities[ticker] = fetch(ticker)
        except Exception as exc:  # source errors retain per-security last-good
            errors.append({"ticker": ticker, "error": str(exc)[:180]})
        if delay_seconds:
            time.sleep(delay_seconds)
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "MOEX ISS",
        "securities": securities,
        "errors": errors,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp.json")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    return output


def data_age_days(as_of: pd.Timestamp, today: date | None = None) -> int:
    today = today or date.today()
    return max(0, (today - as_of.date()).days)
