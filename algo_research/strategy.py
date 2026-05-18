"""Python strategy logic for the active v44 no-leak NQ bot."""

from __future__ import annotations

import pandas as pd


def to_hourly_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Return 1H bars aligned like the NQ RTH hour blocks: 09:30, 10:30, etc."""
    if df.empty:
        return df.copy()

    out = df.copy()
    out["time"] = pd.to_datetime(out["time"], utc=True)
    out = out.sort_values("time").set_index("time")

    # Databento can return ohlcv-1h directly. If bars are already hourly, keep them.
    diffs = out.index.to_series().diff().dropna()
    is_hourly = not diffs.empty and diffs.dt.total_seconds().median() >= 3500
    if is_hourly:
        hourly = out
    else:
        ny_index = out.index.tz_convert("America/New_York")
        out = out.set_index(ny_index)
        hourly = out.resample("60min", origin="start_day", offset="30min", label="left", closed="left").agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        hourly.index = hourly.index.tz_convert("UTC")

    hourly = hourly.dropna(subset=["open", "high", "low", "close"]).reset_index()
    hourly = hourly.rename(columns={hourly.columns[0]: "time"})
    numeric_cols = ["open", "high", "low", "close", "volume"]
    hourly[numeric_cols] = hourly[numeric_cols].apply(pd.to_numeric, errors="coerce")
    return hourly.dropna(subset=numeric_cols).reset_index(drop=True)


def add_v44_indicators(
    df: pd.DataFrame,
    ema_fast_len: int = 50,
    ema_slow_len: int = 200,
    atr_len: int = 14,
    volume_len: int = 30,
    structure_len: int = 20,
    kaufman_len: int = 10,
    min_kaufman_ratio: float = 0.34,
    atr_expansion_mult: float = 1.08,
    atx_len: int = 14,
    min_atx: float = 18.0,
) -> pd.DataFrame:
    """Add the same no-leak setup family used by the current Pine starter."""
    out = to_hourly_bars(df)
    if out.empty:
        return out

    high_low = out["high"] - out["low"]
    high_prev_close = (out["high"] - out["close"].shift(1)).abs()
    low_prev_close = (out["low"] - out["close"].shift(1)).abs()
    true_range = pd.concat([high_low, high_prev_close, low_prev_close], axis=1).max(axis=1)

    out["ema_fast"] = out["close"].ewm(span=ema_fast_len, adjust=False).mean()
    out["ema_slow"] = out["close"].ewm(span=ema_slow_len, adjust=False).mean()
    out["atr"] = true_range.rolling(atr_len).mean()
    out["atr_baseline"] = out["atr"].rolling(100).mean()
    out["atr_ratio"] = out["atr"] / out["atr_baseline"].replace(0, float("nan"))
    out["volume_baseline"] = out["volume"].rolling(volume_len).mean()
    out["relative_volume"] = out["volume"] / out["volume_baseline"].replace(0, float("nan"))
    out["prior_high"] = out["high"].shift(1).rolling(structure_len).max()
    out["prior_low"] = out["low"].shift(1).rolling(structure_len).min()
    out["candle_range"] = (out["high"] - out["low"]).clip(lower=0.25)
    out["body_pct"] = (out["close"] - out["open"]).abs() / out["candle_range"]
    out["close_near_high"] = (out["close"] - out["low"]) / out["candle_range"] >= 0.60
    out["close_near_low"] = (out["high"] - out["close"]) / out["candle_range"] >= 0.60

    directional_change = (out["close"] - out["close"].shift(kaufman_len)).abs()
    path_length = out["close"].diff().abs().rolling(kaufman_len).sum()
    out["kaufman_ratio"] = directional_change / path_length.replace(0, float("nan"))

    up_move = out["high"].diff()
    down_move = -out["low"].diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    tr_rma = true_range.ewm(alpha=1 / atx_len, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / atx_len, adjust=False).mean() / tr_rma.replace(0, float("nan"))
    minus_di = 100 * minus_dm.ewm(alpha=1 / atx_len, adjust=False).mean() / tr_rma.replace(0, float("nan"))
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, float("nan"))) * 100
    out["plus_di"] = plus_di
    out["minus_di"] = minus_di
    out["atx"] = dx.ewm(alpha=1 / atx_len, adjust=False).mean()

    ny_time = pd.to_datetime(out["time"], utc=True).dt.tz_convert("America/New_York")
    out["ny_date"] = ny_time.dt.date.astype(str)
    out["ny_minutes"] = ny_time.dt.hour * 60 + ny_time.dt.minute
    out["in_session"] = (out["ny_minutes"] >= 570) & (out["ny_minutes"] < 960)
    out["near_session_close"] = out["in_session"] & (out["ny_minutes"] >= 950)

    out["bull_trend"] = (out["close"] > out["ema_slow"]) & (out["ema_fast"] > out["ema_slow"])
    out["bear_trend"] = (out["close"] < out["ema_slow"]) & (out["ema_fast"] < out["ema_slow"])
    out["volume_ok"] = out["relative_volume"] >= 0.75
    out["candle_ok"] = out["body_pct"] >= 0.35
    out["volatility_ok"] = out["atr_baseline"].isna() | (out["atr"] <= out["atr_baseline"] * 2.2)
    out["kaufman_long"] = (
        out["bull_trend"]
        & (out["kaufman_ratio"] >= min_kaufman_ratio)
        & (out["close"] > out["ema_fast"])
        & (out["close"] > out["open"])
        & out["volume_ok"]
    )
    out["kaufman_short"] = (
        out["bear_trend"]
        & (out["kaufman_ratio"] >= min_kaufman_ratio)
        & (out["close"] < out["ema_fast"])
        & (out["close"] < out["open"])
        & out["volume_ok"]
    )
    out["atr_long"] = (
        out["bull_trend"]
        & (out["atr_ratio"] >= atr_expansion_mult)
        & out["candle_ok"]
        & out["close_near_high"]
        & out["volume_ok"]
    )
    out["atr_short"] = (
        out["bear_trend"]
        & (out["atr_ratio"] >= atr_expansion_mult)
        & out["candle_ok"]
        & out["close_near_low"]
        & out["volume_ok"]
    )
    out["atx_long"] = (
        out["bull_trend"]
        & (out["atx"] >= min_atx)
        & (out["plus_di"] > out["minus_di"])
        & out["close_near_high"]
        & out["volume_ok"]
    )
    out["atx_short"] = (
        out["bear_trend"]
        & (out["atx"] >= min_atx)
        & (out["minus_di"] > out["plus_di"])
        & out["close_near_low"]
        & out["volume_ok"]
    )
    out["signal"] = "WAIT"
    out.loc[out["kaufman_long"] | out["atr_long"] | out["atx_long"], "signal"] = "BUY"
    out.loc[out["kaufman_short"] | out["atr_short"] | out["atx_short"], "signal"] = "SELL"
    out["setup"] = "WAIT"
    out.loc[out["atx_long"] | out["atx_short"], "setup"] = "ATXTrend"
    out.loc[out["atr_long"] | out["atr_short"], "setup"] = "ATRExpansion"
    out.loc[out["kaufman_long"] | out["kaufman_short"], "setup"] = "KaufmanRatio"
    return out


def score_algorithm(df: pd.DataFrame) -> pd.DataFrame:
    """Backward-compatible alias used by older scripts."""
    return add_v44_indicators(df)
