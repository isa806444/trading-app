"""No-leak Python backtester for the active v44 1H NQ Pine strategy."""

from __future__ import annotations

from dataclasses import dataclass
import json
import random
from pathlib import Path
from typing import Any

import pandas as pd

from .strategy import add_v44_indicators


@dataclass
class BacktestConfig:
    starting_cash: float = 100_000.0
    contract_qty: int = 1
    point_value: float = 20.0
    tick_size: float = 0.25
    atr_stop_mult: float = 1.2
    reward_multiple: float = 2.0
    trail_after_r: float = 1.0
    trail_atr_mult: float = 1.6
    max_trades_per_day: int = 5
    max_losses_per_day: int = 3
    max_consecutive_losses: int = 2
    max_daily_loss_dollars: float = 600.0
    cooldown_bars: int = 6
    fee_per_contract_side: float = 4.0
    slippage_points: float = 0.5
    validation_fraction: float = 0.30
    monte_carlo_runs: int = 500
    random_seed: int = 42


def _apply_slippage(price: float, side: str, is_entry: bool, slippage_points: float) -> float:
    if slippage_points <= 0:
        return price
    if side == "BUY":
        return price + slippage_points if is_entry else price - slippage_points
    return price - slippage_points if is_entry else price + slippage_points


def _trade_pnl(side: str, entry: float, exit_price: float, qty: int, point_value: float, fees: float) -> float:
    multiplier = 1 if side == "BUY" else -1
    return (exit_price - entry) * qty * point_value * multiplier - fees


def _max_losing_streak(trades_df: pd.DataFrame) -> int:
    streak = 0
    worst = 0
    for pnl in trades_df.get("pnl", []):
        if pnl < 0:
            streak += 1
            worst = max(worst, streak)
        else:
            streak = 0
    return worst


def _summarize_trades(trades_df: pd.DataFrame) -> dict[str, Any]:
    if trades_df.empty:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "total_pnl": 0.0,
            "profit_factor": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "expectancy": 0.0,
            "max_losing_streak": 0,
        }

    wins = trades_df[trades_df["pnl"] > 0]
    losses = trades_df[trades_df["pnl"] < 0]
    gross_profit = float(wins["pnl"].sum()) if not wins.empty else 0.0
    gross_loss = float(losses["pnl"].sum()) if not losses.empty else 0.0
    total_trades = int(len(trades_df))
    win_rate = len(wins) / total_trades if total_trades else 0.0
    avg_win = float(wins["pnl"].mean()) if not wins.empty else 0.0
    avg_loss = float(losses["pnl"].mean()) if not losses.empty else 0.0
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss
    return {
        "trades": total_trades,
        "wins": int(len(wins)),
        "losses": int(len(losses)),
        "win_rate": round(win_rate * 100, 2),
        "total_pnl": round(float(trades_df["pnl"].sum()), 2),
        "profit_factor": round(gross_profit / abs(gross_loss), 3) if gross_loss else round(gross_profit, 3),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": round(expectancy, 2),
        "largest_win": round(float(trades_df["pnl"].max()), 2),
        "largest_loss": round(float(trades_df["pnl"].min()), 2),
        "max_losing_streak": _max_losing_streak(trades_df),
    }


def _monte_carlo_trade_resample(trades_df: pd.DataFrame, runs: int, seed: int) -> dict[str, Any]:
    if trades_df.empty or runs <= 0:
        return {"runs": 0, "p05_pnl": 0.0, "median_pnl": 0.0, "p95_pnl": 0.0, "worst_drawdown": 0.0}
    rng = random.Random(seed)
    pnls = [float(value) for value in trades_df["pnl"].tolist()]
    totals = []
    drawdowns = []
    for _ in range(runs):
        running = 0.0
        peak = 0.0
        worst_dd = 0.0
        for _ in pnls:
            running += rng.choice(pnls)
            peak = max(peak, running)
            worst_dd = min(worst_dd, running - peak)
        totals.append(running)
        drawdowns.append(worst_dd)
    totals_series = pd.Series(totals)
    return {
        "runs": runs,
        "p05_pnl": round(float(totals_series.quantile(0.05)), 2),
        "median_pnl": round(float(totals_series.quantile(0.50)), 2),
        "p95_pnl": round(float(totals_series.quantile(0.95)), 2),
        "worst_drawdown": round(min(drawdowns), 2),
    }


def _export_result(result: dict[str, Any], output_dir: str | Path, symbol: str) -> None:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    clean_symbol = symbol.upper().replace(".", "_").replace("!", "").replace("/", "_")
    result["signals"].to_csv(output_path / f"{clean_symbol}_signals.csv", index=False)
    result["trades"].to_csv(output_path / f"{clean_symbol}_trades.csv", index=False)
    result["equity"].to_csv(output_path / f"{clean_symbol}_equity.csv", index=False)
    (output_path / f"{clean_symbol}_summary.json").write_text(
        json.dumps(result["summary"], indent=2),
        encoding="utf-8",
    )


def run_backtest(
    df: pd.DataFrame,
    config: BacktestConfig | None = None,
    *,
    rolling_years: int = 4,
    symbol: str = "NQ.c.0",
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Run the active v44 1H no-leak logic.

    Signal row i-1 is the only information used to place/exit on row i. This
    mirrors the Pine script's request.security(..., lookahead_off) + [1] design.
    """
    config = config or BacktestConfig()
    data = add_v44_indicators(df).dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
    if data.empty:
        result = {"summary": _summarize_trades(pd.DataFrame()), "signals": data, "trades": pd.DataFrame(), "equity": pd.DataFrame()}
        if output_dir:
            _export_result(result, output_dir, symbol)
        return result

    end_time = pd.to_datetime(data["time"], utc=True).max()
    start_time = end_time - pd.DateOffset(years=rolling_years)
    data = data[data["time"] >= start_time].reset_index(drop=True)

    cash = float(config.starting_cash)
    equity_curve: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    position: dict[str, Any] | None = None
    trades_today = 0
    losses_today = 0
    loss_streak = 0
    last_trade_index: int | None = None
    current_day: str | None = None
    day_start_equity = cash

    for i in range(1, len(data)):
        bar = data.iloc[i]
        signal = data.iloc[i - 1]
        bar_day = str(bar["ny_date"])
        if current_day != bar_day:
            current_day = bar_day
            trades_today = 0
            losses_today = 0
            loss_streak = 0
            day_start_equity = cash

        fees_round_turn = config.fee_per_contract_side * config.contract_qty * 2

        def close_position(exit_price: float, reason: str, exit_time: Any) -> None:
            nonlocal cash, position, losses_today, loss_streak, last_trade_index
            if not position:
                return
            slipped_exit = _apply_slippage(exit_price, position["side"], False, config.slippage_points)
            pnl = _trade_pnl(
                position["side"],
                position["entry"],
                slipped_exit,
                position["qty"],
                config.point_value,
                fees_round_turn,
            )
            cash += pnl
            trades.append(
                {
                    "entry_time": position["entry_time"],
                    "exit_time": exit_time,
                    "side": position["side"],
                    "setup": position["setup"],
                    "entry": round(position["entry"], 2),
                    "exit": round(slipped_exit, 2),
                    "stop": round(position["stop"], 2),
                    "target": round(position["target"], 2),
                    "qty": position["qty"],
                    "points": round((slipped_exit - position["entry"]) * (1 if position["side"] == "BUY" else -1), 2),
                    "pnl": round(pnl, 2),
                    "reason": reason,
                    "signal_time": position["signal_time"],
                }
            )
            if pnl < 0:
                losses_today += 1
                loss_streak += 1
            else:
                loss_streak = 0
            position = None
            last_trade_index = i

        # Exit first using only the previously closed 1H signal row.
        if position:
            if position["side"] == "BUY" and signal["close"] < signal["ema_fast"]:
                close_position(float(bar["open"]), "thesis_exit", bar["time"])
            elif position and position["side"] == "SELL" and signal["close"] > signal["ema_fast"]:
                close_position(float(bar["open"]), "thesis_exit", bar["time"])

        # Conservative intrabar order: if stop and target both hit, count the stop.
        if position:
            if position["side"] == "BUY":
                if float(bar["low"]) <= position["stop"]:
                    close_position(position["stop"], "stop", bar["time"])
                elif float(bar["high"]) >= position["target"]:
                    close_position(position["target"], "target", bar["time"])
            elif position["side"] == "SELL":
                if float(bar["high"]) >= position["stop"]:
                    close_position(position["stop"], "stop", bar["time"])
                elif float(bar["low"]) <= position["target"]:
                    close_position(position["target"], "target", bar["time"])

        if position:
            if position["side"] == "BUY":
                open_profit_points = float(bar["close"]) - position["entry"]
                if open_profit_points >= position["risk_points"] * config.trail_after_r:
                    position["stop"] = max(
                        position["stop"],
                        float(bar["close"]) - float(signal["atr"]) * config.trail_atr_mult,
                        position["entry"],
                    )
            else:
                open_profit_points = position["entry"] - float(bar["close"])
                if open_profit_points >= position["risk_points"] * config.trail_after_r:
                    position["stop"] = min(
                        position["stop"],
                        float(bar["close"]) + float(signal["atr"]) * config.trail_atr_mult,
                        position["entry"],
                    )

        daily_pnl = cash - day_start_equity
        daily_loss_lock = daily_pnl <= -config.max_daily_loss_dollars
        loss_lock = losses_today >= config.max_losses_per_day or loss_streak >= config.max_consecutive_losses
        trade_count_lock = trades_today >= config.max_trades_per_day
        cooldown_ok = last_trade_index is None or i - last_trade_index > config.cooldown_bars
        gate = (
            bool(signal["in_session"])
            and not bool(signal["near_session_close"])
            and bool(signal["volatility_ok"])
            and not daily_loss_lock
            and not loss_lock
            and not trade_count_lock
            and cooldown_ok
        )

        if position is None and gate and signal["signal"] in {"BUY", "SELL"}:
            side = str(signal["signal"])
            signal_close = float(signal["close"])
            atr = float(signal["atr"])
            if side == "BUY":
                atr_stop = signal_close - atr * config.atr_stop_mult
                structure_stop = float(signal["prior_low"]) - config.tick_size * 2
                stop = max(atr_stop, structure_stop)
                risk_points = max(signal_close - stop, config.tick_size)
                target = signal_close + risk_points * config.reward_multiple
                entry = _apply_slippage(float(bar["open"]), side, True, config.slippage_points)
            else:
                atr_stop = signal_close + atr * config.atr_stop_mult
                structure_stop = float(signal["prior_high"]) + config.tick_size * 2
                stop = min(atr_stop, structure_stop)
                risk_points = max(stop - signal_close, config.tick_size)
                target = signal_close - risk_points * config.reward_multiple
                entry = _apply_slippage(float(bar["open"]), side, True, config.slippage_points)

            position = {
                "entry_time": bar["time"],
                "signal_time": signal["time"],
                "side": side,
                "setup": signal["setup"],
                "entry": entry,
                "qty": config.contract_qty,
                "stop": stop,
                "target": target,
                "risk_points": risk_points,
            }
            trades_today += 1
            last_trade_index = i

        open_pnl = 0.0
        if position:
            open_pnl = _trade_pnl(
                position["side"],
                position["entry"],
                float(bar["close"]),
                position["qty"],
                config.point_value,
                0.0,
            )
        equity_curve.append({"time": bar["time"], "equity": round(cash + open_pnl, 2)})

    trades_df = pd.DataFrame(trades)
    equity_df = pd.DataFrame(equity_curve)
    if equity_df.empty:
        max_drawdown_dollars = 0.0
        max_drawdown_pct = 0.0
    else:
        peak = equity_df["equity"].cummax()
        drawdown = equity_df["equity"] - peak
        max_drawdown_dollars = float(drawdown.min())
        max_drawdown_pct = float((drawdown / peak).min() * 100)

    total_trades = int(len(trades_df))
    split_index = int(total_trades * max(0.0, min(1.0, 1 - config.validation_fraction)))
    summary = _summarize_trades(trades_df)
    summary.update(
        {
            "symbol": symbol,
            "logic": "v44_1h_no_leak_kaufman_atr_atx",
            "starting_cash": round(config.starting_cash, 2),
            "ending_cash": round(cash, 2),
            "max_drawdown_dollars": round(max_drawdown_dollars, 2),
            "max_drawdown_pct": round(max_drawdown_pct, 2),
            "rolling_years": rolling_years,
            "backtest_start": str(start_time),
            "backtest_end": str(end_time),
            "contract_qty": config.contract_qty,
            "point_value": config.point_value,
            "fee_per_contract_side": config.fee_per_contract_side,
            "slippage_points": config.slippage_points,
            "in_sample": _summarize_trades(trades_df.iloc[:split_index]) if total_trades else _summarize_trades(trades_df),
            "out_of_sample": _summarize_trades(trades_df.iloc[split_index:]) if total_trades else _summarize_trades(trades_df),
            "monte_carlo": _monte_carlo_trade_resample(trades_df, config.monte_carlo_runs, config.random_seed),
        }
    )
    result = {"summary": summary, "signals": data, "trades": trades_df, "equity": equity_df}
    if output_dir:
        _export_result(result, output_dir, symbol)
    return result
