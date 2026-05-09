"""Simple event-loop backtester for the AI algorithm."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .strategy import score_algorithm


@dataclass
class BacktestConfig:
    starting_cash: float = 10_000
    risk_fraction: float = 0.1
    stop_pct: float = 0.01
    target_pct: float = 0.02


def run_backtest(df: pd.DataFrame, config: BacktestConfig | None = None) -> dict:
    config = config or BacktestConfig()
    data = score_algorithm(df).dropna().reset_index(drop=True)
    cash = config.starting_cash
    equity_curve = []
    trades = []
    position = None

    for _, row in data.iterrows():
        price = float(row["close"])
        high = float(row["high"])
        low = float(row["low"])

        if position:
            side = position["side"]
            stop = position["stop"]
            target = position["target"]
            exit_price = None
            reason = None
            if side == "BUY" and low <= stop:
                exit_price = stop
                reason = "stop"
            elif side == "BUY" and high >= target:
                exit_price = target
                reason = "target"
            elif side == "SELL" and high >= stop:
                exit_price = stop
                reason = "stop"
            elif side == "SELL" and low <= target:
                exit_price = target
                reason = "target"
            elif row["signal"] != side and row["signal"] != "WAIT":
                exit_price = price
                reason = "reverse"

            if exit_price is not None:
                multiplier = 1 if side == "BUY" else -1
                pnl = (exit_price - position["entry"]) * position["qty"] * multiplier
                cash += pnl
                trades.append({
                    "entry_time": position["time"],
                    "exit_time": row["time"],
                    "side": side,
                    "entry": position["entry"],
                    "exit": exit_price,
                    "qty": position["qty"],
                    "pnl": pnl,
                    "reason": reason
                })
                position = None

        if position is None and row["signal"] in {"BUY", "SELL"}:
            notional = cash * config.risk_fraction
            qty = max(notional / price, 0)
            if row["signal"] == "BUY":
                stop = price * (1 - config.stop_pct)
                target = price * (1 + config.target_pct)
            else:
                stop = price * (1 + config.stop_pct)
                target = price * (1 - config.target_pct)
            position = {
                "time": row["time"],
                "side": row["signal"],
                "entry": price,
                "qty": qty,
                "stop": stop,
                "target": target
            }

        open_pnl = 0
        if position:
            multiplier = 1 if position["side"] == "BUY" else -1
            open_pnl = (price - position["entry"]) * position["qty"] * multiplier
        equity_curve.append({"time": row["time"], "equity": cash + open_pnl})

    trades_df = pd.DataFrame(trades)
    equity_df = pd.DataFrame(equity_curve)
    if equity_df.empty:
        max_drawdown = 0
    else:
        peak = equity_df["equity"].cummax()
        max_drawdown = float(((equity_df["equity"] - peak) / peak).min() * 100)

    total_pnl = float(trades_df["pnl"].sum()) if not trades_df.empty else 0
    wins = int((trades_df["pnl"] > 0).sum()) if not trades_df.empty else 0
    total_trades = int(len(trades_df))
    return {
        "summary": {
            "starting_cash": config.starting_cash,
            "ending_cash": round(cash, 2),
            "total_pnl": round(total_pnl, 2),
            "trades": total_trades,
            "wins": wins,
            "win_rate": round((wins / total_trades) * 100, 2) if total_trades else 0,
            "max_drawdown_pct": round(max_drawdown, 2)
        },
        "signals": data,
        "trades": trades_df,
        "equity": equity_df
    }
