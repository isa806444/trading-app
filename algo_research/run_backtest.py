"""CLI entry point for VS Code backtesting."""

from __future__ import annotations

import argparse
from pathlib import Path

from .backtest import BacktestConfig, run_backtest
from .databento_data import fetch_ohlcv


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the AI algorithm backtest on Databento OHLCV data.")
    parser.add_argument("--symbol", default="ESM6")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--dataset", default="GLBX.MDP3")
    parser.add_argument("--schema", default="ohlcv-1m")
    parser.add_argument("--cash", type=float, default=10_000)
    parser.add_argument("--risk-fraction", type=float, default=0.1)
    parser.add_argument("--stop-pct", type=float, default=0.01)
    parser.add_argument("--target-pct", type=float, default=0.02)
    parser.add_argument("--output-dir", default="data/backtests")
    args = parser.parse_args()

    bars = fetch_ohlcv(args.symbol, args.start, args.end, args.dataset, args.schema)
    result = run_backtest(
        bars,
        BacktestConfig(
            starting_cash=args.cash,
            risk_fraction=args.risk_fraction,
            stop_pct=args.stop_pct,
            target_pct=args.target_pct,
        )
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result["signals"].to_csv(output_dir / f"{args.symbol.upper()}_signals.csv", index=False)
    result["trades"].to_csv(output_dir / f"{args.symbol.upper()}_trades.csv", index=False)
    result["equity"].to_csv(output_dir / f"{args.symbol.upper()}_equity.csv", index=False)
    print(result["summary"])


if __name__ == "__main__":
    main()
