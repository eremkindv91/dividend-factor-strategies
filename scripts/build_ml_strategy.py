#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ml_strategy.data import (  # noqa: E402
    refresh_cbr_key_rate_cache,
    refresh_dividend_cache,
    refresh_index_cache,
    refresh_moex_instrument_cache,
)
from ml_strategy.pipeline import run_pipeline  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Build point-in-time ML strategy snapshots from real MOEX data.")
    parser.add_argument("--allow-network", action="store_true", help="Refresh official index/dividend caches.")
    parser.add_argument("--challengers", action="store_true", help="Evaluate optional CatBoost/LightGBM challengers.")
    args = parser.parse_args()
    daily = ROOT / "data" / "daily"
    if args.allow_network:
        benchmarks = daily / "benchmarks"
        for secid in ("MCFTR", "IMOEX", "RGBI"):
            refresh_index_cache(benchmarks / f"{secid}.parquet", secid)
        for secid in (
            "MOEXOG", "MOEXMM", "MOEXFN", "MOEXRE", "MOEXEU",
            "MOEXCN", "MOEXIT", "MOEXTL", "MOEXTN", "MOEXCH",
        ):
            output = benchmarks / f"{secid}.parquet"
            try:
                refresh_index_cache(output, secid)
            except Exception as error:  # optional sector input must not block the core model
                state = "preserving previous cache" if output.exists() else "pack remains unavailable"
                print(f"[ml-strategy] {secid} refresh failed; {state}: {error}", file=sys.stderr)
        refresh_moex_instrument_cache(
            benchmarks / "USDRUB.parquet",
            {
                "id": "USD000UTSTOM",
                "engine": "currency",
                "market": "selt",
                "board": "CETS",
            },
            "2018-01-01",
        )
        refresh_cbr_key_rate_cache(benchmarks / "KEY_RATE.parquet")
        tickers = [path.stem.upper() for path in (daily / "prices").glob("*.parquet")]
        refresh_dividend_cache(tickers, daily / "dividends.json")
    bundle = run_pipeline(ROOT, include_tree_challengers=args.challengers)
    latest = bundle["latest.json"]
    print(
        "[ml-strategy] "
        f"{latest['data_as_of']} action={latest['signal']['action']} "
        f"positions={len(latest['portfolio']['positions'])} "
        f"model={latest['model']['champion']}:{latest['model']['status']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
