"""CLI entry point for the v44 no-leak NQ Python backtest."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

import pandas as pd

from .backtest import BacktestConfig, run_backtest
from .databento_data import fetch_ohlcv, load_local_env, save_ohlcv_csv


def _default_end(schema: str, delay_hours: float) -> str:
    now = pd.Timestamp(datetime.now(timezone.utc)) - pd.Timedelta(hours=max(delay_hours, 0.0))
    if "1h" in schema.lower():
        return now.floor("h").isoformat()
    if "1m" in schema.lower():
        return now.floor("min").isoformat()
    return now.floor("s").isoformat()


def _default_start(years: int, end: str) -> str:
    return (pd.Timestamp(end) - pd.DateOffset(years=years)).isoformat()


def _load_or_fetch(args: argparse.Namespace) -> pd.DataFrame:
    if args.input_csv:
        print(f"Loading input candles: {args.input_csv}")
        return pd.read_csv(args.input_csv)

    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    clean_symbol = args.symbol.upper().replace(".", "_").replace("!", "").replace("/", "_")
    cache_file = cache_dir / f"{clean_symbol}_{args.schema}_{args.start[:10]}_{args.end[:10]}.csv"

    if cache_file.exists() and not args.refresh:
        print(f"Loading cached Databento bars: {cache_file}")
        return pd.read_csv(cache_file)

    api_key = os.environ.get("DATABENTO_API_KEY", "").strip()
    if not api_key or api_key.startswith("your_"):
        raise RuntimeError(
            "DATABENTO_API_KEY is not set locally. Add it to .env or pass --input-csv with saved OHLCV candles."
        )

    print("Fetching Databento bars...")
    bars = fetch_ohlcv(
        args.symbol,
        args.start,
        args.end,
        args.dataset,
        args.schema,
        stype_in=args.stype_in,
    )
    bars.to_csv(cache_file, index=False)
    save_ohlcv_csv(bars, args.symbol, args.cache_dir)
    print(f"Saved {len(bars)} source bars to {cache_file}")
    return bars


def main() -> None:
    load_local_env()
    parser = argparse.ArgumentParser(description="Run the active v44 1H no-leak NQ backtest.")
    parser.add_argument("--symbol", default="NQ.c.0", help="Use NQ.c.0 continuous for 4-year testing, or NQM6 for a single contract.")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--years", type=int, default=4)
    parser.add_argument(
        "--end-delay-hours",
        type=float,
        default=float(os.environ.get("DATABENTO_BACKTEST_END_DELAY_HOURS", "10")),
        help="Delay default end time for data entitlements. Use 0 if your Databento plan has live access.",
    )
    parser.add_argument("--dataset", default="GLBX.MDP3")
    parser.add_argument("--schema", default="ohlcv-1h")
    parser.add_argument("--stype-in", default="continuous")
    parser.add_argument("--cash", type=float, default=100_000)
    parser.add_argument("--contracts", type=int, default=1)
    parser.add_argument("--point-value", type=float, default=20.0)
    parser.add_argument("--slippage-points", type=float, default=0.5)
    parser.add_argument("--fee-per-contract-side", type=float, default=4.0)
    parser.add_argument("--validation-fraction", type=float, default=0.30)
    parser.add_argument("--monte-carlo-runs", type=int, default=500)
    parser.add_argument("--cache-dir", default="data/databento")
    parser.add_argument("--output-dir", default="data/backtests")
    parser.add_argument("--input-csv", default=None, help="Use an existing OHLCV CSV instead of fetching Databento.")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    args.end = args.end or _default_end(args.schema, args.end_delay_hours)
    args.start = args.start or _default_start(args.years, args.end)

    try:
        bars = _load_or_fetch(args)
    except RuntimeError as exc:
        print(f"Backtest setup blocked: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    result = run_backtest(
        bars,
        BacktestConfig(
            starting_cash=args.cash,
            contract_qty=args.contracts,
            point_value=args.point_value,
            slippage_points=args.slippage_points,
            fee_per_contract_side=args.fee_per_contract_side,
            validation_fraction=args.validation_fraction,
            monte_carlo_runs=args.monte_carlo_runs,
        ),
        rolling_years=args.years,
        symbol=args.symbol,
        output_dir=args.output_dir,
    )
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
