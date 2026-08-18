from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def market_repo(tmp_path: Path) -> Path:
    repo = tmp_path
    source_config = Path(__file__).resolve().parents[2] / "config" / "ml_strategy"
    shutil.copytree(source_config, repo / "config" / "ml_strategy")
    (repo / "data" / "daily" / "prices").mkdir(parents=True)
    (repo / "data" / "daily" / "benchmarks").mkdir(parents=True)
    dates = pd.bdate_range("2022-01-03", periods=760)
    securities = []
    sectors = (
        "Нефть и газ",
        "Металлы и добыча",
        "Финансы (Банки)",
        "Недвижимость",
        "IT",
        "Электроэнергетика",
        "Потребительский сектор",
        "Телекоммуникации",
        "Транспорт",
        "Химия",
        "Финансы",
    )
    for number in range(18):
        ticker = f"T{number:02d}"
        sector = sectors[number % len(sectors)]
        securities.append(
            {
                "canonical_ticker": ticker,
                "secid": ticker,
                "name": f"Company {number:02d}",
                "sector": sector,
                "board": "TQBR",
                "instrument_type": "share",
                "status": "active",
                "lot_size": 10,
            }
        )
        time = np.arange(len(dates), dtype=float)
        market_component = 0.0003 + 0.002 * np.sin(time / 19)
        security_component = (number - 8.5) * 0.000008 + 0.001 * np.sin(time / (7 + number / 3))
        daily_return = market_component + security_component
        close = (50 + number * 4) * np.cumprod(1 + daily_return)
        frame = pd.DataFrame(
            {
                "trade_date": dates,
                "open": close * 0.998,
                "high": close * 1.005,
                "low": close * 0.995,
                "close": close,
                "value": 30_000_000 + number * 2_000_000 + 1_000_000 * np.sin(time / 11),
                "volume": 100_000 + number * 1_000 + 100 * np.cos(time / 9),
            }
        )
        frame.to_parquet(repo / "data" / "daily" / "prices" / f"{ticker}.parquet", index=False)
    master = {
        "schema_version": 1,
        "securities": securities,
    }
    (repo / "data" / "security_master.json").write_text(json.dumps(master), encoding="utf-8")
    benchmark_close = 1000 * np.cumprod(1 + 0.00025 + 0.0015 * np.sin(np.arange(len(dates)) / 19))
    pd.DataFrame({"trade_date": dates, "close": benchmark_close}).to_parquet(
        repo / "data" / "daily" / "benchmarks" / "MCFTR.parquet", index=False
    )
    for name, values in {
        "IMOEX": benchmark_close * 0.45,
        "RGBI": 100 + 0.01 * np.arange(len(dates)),
        "USDRUB": 70 + 0.015 * np.arange(len(dates)),
        "KEY_RATE": np.full(len(dates), 12.0),
        "MOEXOG": 1200 * np.cumprod(1 + 0.00035 + 0.0018 * np.sin(np.arange(len(dates)) / 23)),
        "MOEXMM": 900 * np.cumprod(1 + 0.00020 + 0.0021 * np.sin(np.arange(len(dates)) / 17)),
        "MOEXFN": 1100 * np.cumprod(1 + 0.00030 + 0.0016 * np.sin(np.arange(len(dates)) / 29)),
        "MOEXRE": 700 * np.cumprod(1 + 0.00010 + 0.0024 * np.sin(np.arange(len(dates)) / 13)),
        "MOEXEU": 800 * np.cumprod(1 + 0.00022 + 0.0014 * np.sin(np.arange(len(dates)) / 31)),
        "MOEXCN": 950 * np.cumprod(1 + 0.00018 + 0.0017 * np.sin(np.arange(len(dates)) / 27)),
        "MOEXIT": 600 * np.cumprod(1 + 0.00040 + 0.0022 * np.sin(np.arange(len(dates)) / 21)),
        "MOEXTL": 750 * np.cumprod(1 + 0.00014 + 0.0012 * np.sin(np.arange(len(dates)) / 25)),
        "MOEXTN": 680 * np.cumprod(1 + 0.00025 + 0.0019 * np.sin(np.arange(len(dates)) / 15)),
        "MOEXCH": 720 * np.cumprod(1 + 0.00016 + 0.0018 * np.sin(np.arange(len(dates)) / 33)),
    }.items():
        value_column = "rate" if name == "KEY_RATE" else "close"
        pd.DataFrame({"trade_date": dates, value_column: values}).to_parquet(
            repo / "data" / "daily" / "benchmarks" / f"{name}.parquet", index=False
        )
    dividends = {
        "schema_version": 1,
        "securities": {
            ticker: [{"registryclosedate": "2023-07-03", "value": 1.0, "currencyid": "RUB"}]
            for ticker in ("T00", "T01", "T02")
        },
    }
    (repo / "data" / "daily" / "dividends.json").write_text(json.dumps(dividends), encoding="utf-8")
    return repo
