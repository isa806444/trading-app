"""Python version of the app's AI momentum algorithm."""

from __future__ import annotations

import numpy as np
import pandas as pd


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["ema9"] = out["close"].ewm(span=9, adjust=False).mean()
    out["ema20"] = out["close"].ewm(span=20, adjust=False).mean()
    typical = (out["high"] + out["low"] + out["close"]) / 3
    out["vwap"] = (typical * out["volume"]).cumsum() / out["volume"].replace(0, np.nan).cumsum()
    delta = out["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    out["rsi"] = 100 - (100 / (1 + rs))
    out["relative_volume"] = out["volume"] / out["volume"].rolling(30).mean().replace(0, np.nan)
    out["recent_move"] = out["close"].pct_change(8) * 100
    out["recent_high"] = out["high"].shift(1).rolling(8).max()
    out["recent_low"] = out["low"].shift(1).rolling(8).min()
    out["higher_lows"] = out["low"] >= out["low"].shift(2)
    out["lower_highs"] = out["high"] <= out["high"].shift(2)
    return out


def score_algorithm(df: pd.DataFrame) -> pd.DataFrame:
    out = add_indicators(df)
    out["buy_score"] = 35.0
    out["sell_score"] = 35.0

    bullish_stack = (out["close"] > out["ema9"]) & (out["ema9"] > out["ema20"])
    bearish_stack = (out["close"] < out["ema9"]) & (out["ema9"] < out["ema20"])
    out.loc[bullish_stack, "buy_score"] += 16
    out.loc[bearish_stack, "sell_score"] += 16
    out.loc[(~bullish_stack) & (out["close"] > out["ema20"]), "buy_score"] += 7
    out.loc[(~bearish_stack) & (out["close"] < out["ema20"]), "sell_score"] += 7

    out.loc[out["close"] > out["vwap"], "buy_score"] += 8
    out.loc[out["close"] < out["vwap"], "sell_score"] += 8
    out.loc[out["rsi"] >= 58, "buy_score"] += ((out["rsi"] - 50) * 0.65).clip(4, 16)
    out.loc[out["rsi"] <= 42, "sell_score"] += ((50 - out["rsi"]) * 0.65).clip(4, 16)
    out.loc[out["recent_move"] >= 0.4, "buy_score"] += 10
    out.loc[out["recent_move"] <= -0.4, "sell_score"] += 10
    out.loc[out["higher_lows"], "buy_score"] += 8
    out.loc[out["lower_highs"], "sell_score"] += 8
    out.loc[out["close"] > out["recent_high"], "buy_score"] += 12
    out.loc[out["close"] < out["recent_low"], "sell_score"] += 12

    high_rv = out["relative_volume"] >= 1.35
    out.loc[high_rv & (out["buy_score"] >= out["sell_score"]), "buy_score"] += 11
    out.loc[high_rv & (out["sell_score"] > out["buy_score"]), "sell_score"] += 11
    out.loc[out["relative_volume"] < 0.65, ["buy_score", "sell_score"]] -= 5

    out["buy_score"] = out["buy_score"].clip(0, 100).round(1)
    out["sell_score"] = out["sell_score"].clip(0, 100).round(1)
    out["edge"] = (out["buy_score"] - out["sell_score"]).round(1)
    out["signal"] = "WAIT"
    out.loc[(out["edge"] >= 18) & (out["buy_score"] >= 58), "signal"] = "BUY"
    out.loc[(out["edge"] <= -18) & (out["sell_score"] >= 58), "signal"] = "SELL"
    out["grade"] = "C"
    out.loc[(out["signal"] != "WAIT") & (out["edge"].abs() >= 18), "grade"] = "B"
    out.loc[(out["signal"] != "WAIT") & (out["edge"].abs() >= 32) & (out[["buy_score", "sell_score"]].max(axis=1) >= 72), "grade"] = "A"
    return out
