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
    ema_pullback_len: int = 21,
    atr_len: int = 14,
    volume_len: int = 30,
    structure_len: int = 20,
    kaufman_len: int = 10,
    min_kaufman_ratio: float = 0.30,
    atr_expansion_mult: float = 1.03,
    dmi_len: int = 14,
    min_trend_strength: float = 15.0,
    min_relative_volume: float = 0.65,
    min_body_pct: float = 0.25,
    min_setup_score: float = 62.0,
    max_chop_score: float = 72.0,
) -> pd.DataFrame:
    """Add the same no-leak setup family used by the current Pine strategy."""
    out = to_hourly_bars(df)
    if out.empty:
        return out

    high_low = out["high"] - out["low"]
    high_prev_close = (out["high"] - out["close"].shift(1)).abs()
    low_prev_close = (out["low"] - out["close"].shift(1)).abs()
    true_range = pd.concat([high_low, high_prev_close, low_prev_close], axis=1).max(axis=1)

    out["ema_fast"] = out["close"].ewm(span=ema_fast_len, adjust=False).mean()
    out["ema_slow"] = out["close"].ewm(span=ema_slow_len, adjust=False).mean()
    out["ema_pullback"] = out["close"].ewm(span=ema_pullback_len, adjust=False).mean()
    out["atr"] = true_range.rolling(atr_len).mean()
    out["atr_baseline"] = out["atr"].rolling(100).mean()
    out["atr_ratio"] = out["atr"] / out["atr_baseline"].replace(0, float("nan"))
    out["volume_baseline"] = out["volume"].rolling(volume_len).mean()
    out["relative_volume"] = out["volume"] / out["volume_baseline"].replace(0, float("nan"))
    out["prior_high"] = out["high"].shift(1).rolling(structure_len).max()
    out["prior_low"] = out["low"].shift(1).rolling(structure_len).min()
    out["range_high"] = out["high"].rolling(structure_len).max()
    out["range_low"] = out["low"].rolling(structure_len).min()
    out["candle_range"] = (out["high"] - out["low"]).clip(lower=0.25)
    out["body_pct"] = (out["close"] - out["open"]).abs() / out["candle_range"]
    out["upper_wick"] = out["high"] - out[["open", "close"]].max(axis=1)
    out["lower_wick"] = out[["open", "close"]].min(axis=1) - out["low"]
    out["close_near_high"] = (out["close"] - out["low"]) / out["candle_range"] >= 0.60
    out["close_near_low"] = (out["high"] - out["close"]) / out["candle_range"] >= 0.60
    overlap = pd.concat([out["high"], out["high"].shift(1)], axis=1).min(axis=1) - pd.concat(
        [out["low"], out["low"].shift(1)], axis=1
    ).max(axis=1)
    out["candle_overlap"] = overlap.clip(lower=0) / out["candle_range"]
    out["alternating_candle"] = ((out["close"] > out["open"]) & (out["close"].shift(1) < out["open"].shift(1))) | (
        (out["close"] < out["open"]) & (out["close"].shift(1) > out["open"].shift(1))
    )
    directional_change = (out["close"] - out["close"].shift(kaufman_len)).abs()
    path_length = out["close"].diff().abs().rolling(kaufman_len).sum()
    out["kaufman_ratio"] = directional_change / path_length.replace(0, float("nan"))

    up_move = out["high"].diff()
    down_move = -out["low"].diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    tr_rma = true_range.ewm(alpha=1 / dmi_len, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / dmi_len, adjust=False).mean() / tr_rma.replace(0, float("nan"))
    minus_di = 100 * minus_dm.ewm(alpha=1 / dmi_len, adjust=False).mean() / tr_rma.replace(0, float("nan"))
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, float("nan"))) * 100
    out["plus_di"] = plus_di
    out["minus_di"] = minus_di
    out["trend_strength"] = dx.ewm(alpha=1 / dmi_len, adjust=False).mean()

    ny_time = pd.to_datetime(out["time"], utc=True).dt.tz_convert("America/New_York")
    out["ny_date"] = ny_time.dt.date.astype(str)
    out["ny_minutes"] = ny_time.dt.hour * 60 + ny_time.dt.minute
    out["in_session"] = (out["ny_minutes"] >= 570) & (out["ny_minutes"] < 960)
    out["near_session_close"] = out["in_session"] & (out["ny_minutes"] >= 950)
    day_pv = (out["close"] * out["volume"]).groupby(out["ny_date"]).cumsum()
    day_volume = out["volume"].groupby(out["ny_date"]).cumsum().replace(0, float("nan"))
    out["vwap"] = day_pv / day_volume
    out["vwap"] = out["vwap"].fillna(out["ema_pullback"])
    out["vwap_slope"] = (out["vwap"] - out["vwap"].shift(3)) / out["atr"].clip(lower=0.25)
    bb_basis = out["close"].rolling(20).mean()
    bb_dev = out["close"].rolling(20).std() * 2.0
    out["bb_width"] = ((bb_dev * 2.0) / bb_basis.replace(0, float("nan"))) * 100
    out["bb_width_avg"] = out["bb_width"].rolling(20).mean()

    # Conservative 4H confirmation: shift the resampled features so no 1H row
    # can use the currently forming 4H candle.
    htf = out[["time", "close"]].dropna().set_index("time").sort_index()
    htf = htf.resample("4h", label="right", closed="right").agg({"close": "last"}).dropna()
    htf["htf_close"] = htf["close"]
    htf["htf_ema_fast"] = htf["close"].ewm(span=ema_fast_len, adjust=False).mean()
    htf["htf_ema_slow"] = htf["close"].ewm(span=ema_slow_len, adjust=False).mean()
    htf = htf[["htf_close", "htf_ema_fast", "htf_ema_slow"]].shift(1).dropna().reset_index()
    out = pd.merge_asof(out.sort_values("time"), htf, on="time", direction="backward")

    out["bull_trend"] = (out["close"] > out["ema_slow"]) & (out["ema_fast"] > out["ema_slow"])
    out["bear_trend"] = (out["close"] < out["ema_slow"]) & (out["ema_fast"] < out["ema_slow"])
    out["htf_bull"] = (out["htf_close"] > out["htf_ema_slow"]) & (out["htf_ema_fast"] >= out["htf_ema_slow"])
    out["htf_bear"] = (out["htf_close"] < out["htf_ema_slow"]) & (out["htf_ema_fast"] <= out["htf_ema_slow"])
    out["volume_ok"] = out["relative_volume"] >= min_relative_volume
    out["candle_ok"] = out["body_pct"] >= min_body_pct
    out["volatility_ok"] = out["atr_baseline"].isna() | (out["atr"] <= out["atr_baseline"] * 2.35)
    out["dist_vwap_atr"] = (out["close"] - out["vwap"]).abs() / out["atr"].clip(lower=0.25)
    out["range_span"] = (out["range_high"] - out["range_low"]).clip(lower=0.25)
    out["range_position"] = (out["close"] - out["range_low"]) / out["range_span"]
    out["middle_of_range"] = (out["range_position"] > 0.35) & (out["range_position"] < 0.65) & (out["dist_vwap_atr"] > 0.45)
    out["chop_score"] = (
        18
        + (out["trend_strength"] < min_trend_strength).astype(float) * 18
        + (out["vwap_slope"].abs() < 0.03).astype(float) * 10
        + (out["candle_overlap"] > 0.52).astype(float) * 12
        + out["alternating_candle"].astype(float) * 8
        + (out["bb_width"] < out["bb_width_avg"]).astype(float) * 8
        + out["middle_of_range"].astype(float) * 10
        - ((out["kaufman_ratio"] >= min_kaufman_ratio) & (out["trend_strength"] >= min_trend_strength)).astype(float) * 16
    ).clip(lower=0, upper=100)
    out["reclaim_long"] = (out["low"] <= out["vwap"]) & (out["close"] > out["vwap"]) & (out["close"] > out["open"])
    out["reject_short"] = (out["high"] >= out["vwap"]) & (out["close"] < out["vwap"]) & (out["close"] < out["open"])
    out["break_long"] = (out["close"] > out["prior_high"]) & out["close_near_high"] & out["volume_ok"]
    out["break_short"] = (out["close"] < out["prior_low"]) & out["close_near_low"] & out["volume_ok"]
    out["sweep_long"] = (out["low"] < out["prior_low"]) & (out["close"] > out["prior_low"]) & (out["lower_wick"] > out["upper_wick"])
    out["sweep_short"] = (out["high"] > out["prior_high"]) & (out["close"] < out["prior_high"]) & (out["upper_wick"] > out["lower_wick"])
    body = (out["close"] - out["open"]).abs().clip(lower=0.25)
    out["fakeout_long_risk"] = (out["high"] > out["prior_high"]) & (out["close"] < out["prior_high"]) & (out["upper_wick"] > body * 1.2)
    out["fakeout_short_risk"] = (out["low"] < out["prior_low"]) & (out["close"] > out["prior_low"]) & (out["lower_wick"] > body * 1.2)
    out["do_not_chase_long"] = (out["dist_vwap_atr"] > 2.4) | ((out["close"] > out["prior_high"]) & (out["upper_wick"] > body * 1.5))
    out["do_not_chase_short"] = (out["dist_vwap_atr"] > 2.4) | ((out["close"] < out["prior_low"]) & (out["lower_wick"] > body * 1.5))
    out["reversal_long"] = (
        (
            out["sweep_long"]
            | out["reclaim_long"]
            | ((out["low"] <= out["ema_pullback"] + out["atr"] * 0.35) & (out["close"] > out["ema_pullback"]))
        )
        & (out["close"] > out["open"])
        & out["close_near_high"]
        & (out["lower_wick"] > out["upper_wick"])
        & out["volume_ok"]
        & (out["htf_bull"] | out["sweep_long"])
    )
    out["reversal_short"] = (
        (
            out["sweep_short"]
            | out["reject_short"]
            | ((out["high"] >= out["ema_pullback"] - out["atr"] * 0.35) & (out["close"] < out["ema_pullback"]))
        )
        & (out["close"] < out["open"])
        & out["close_near_low"]
        & (out["upper_wick"] > out["lower_wick"])
        & out["volume_ok"]
        & (out["htf_bear"] | out["sweep_short"])
    )
    out["kaufman_long"] = (
        out["bull_trend"]
        & out["htf_bull"]
        & (out["kaufman_ratio"] >= min_kaufman_ratio)
        & (out["close"] > out["ema_fast"])
        & (out["close"] > out["open"])
        & out["volume_ok"]
    )
    out["kaufman_short"] = (
        out["bear_trend"]
        & out["htf_bear"]
        & (out["kaufman_ratio"] >= min_kaufman_ratio)
        & (out["close"] < out["ema_fast"])
        & (out["close"] < out["open"])
        & out["volume_ok"]
    )
    out["atr_long"] = (
        out["bull_trend"]
        & out["htf_bull"]
        & (out["atr_ratio"] >= atr_expansion_mult)
        & out["candle_ok"]
        & out["close_near_high"]
        & out["volume_ok"]
        & (out["break_long"] | out["reclaim_long"] | (out["close"] > out["ema_fast"]))
    )
    out["atr_short"] = (
        out["bear_trend"]
        & out["htf_bear"]
        & (out["atr_ratio"] >= atr_expansion_mult)
        & out["candle_ok"]
        & out["close_near_low"]
        & out["volume_ok"]
        & (out["break_short"] | out["reject_short"] | (out["close"] < out["ema_fast"]))
    )
    out["long_score"] = (
        42
        + out["bull_trend"].astype(float) * 12
        + out["htf_bull"].astype(float) * 10
        + (out["close"] > out["vwap"]).astype(float) * 8
        + (out["vwap_slope"] > 0).astype(float) * 5
        + out["volume_ok"].astype(float) * 7
        + (out["relative_volume"] >= 1.1).astype(float) * 5
        + (out["kaufman_ratio"] >= min_kaufman_ratio).astype(float) * 8
        + (out["trend_strength"] >= min_trend_strength).astype(float) * 6
        + out["close_near_high"].astype(float) * 5
        + (out["reclaim_long"] | out["sweep_long"]).astype(float) * 8
        + out["reversal_long"].astype(float) * 9
        + out["break_long"].astype(float) * 6
        - (out["chop_score"] > max_chop_score).astype(float) * 12
        - out["fakeout_long_risk"].astype(float) * 12
        - out["do_not_chase_long"].astype(float) * 10
        - (out["bear_trend"] & out["htf_bear"]).astype(float) * 12
    ).clip(lower=0, upper=100)
    out["short_score"] = (
        42
        + out["bear_trend"].astype(float) * 12
        + out["htf_bear"].astype(float) * 10
        + (out["close"] < out["vwap"]).astype(float) * 8
        + (out["vwap_slope"] < 0).astype(float) * 5
        + out["volume_ok"].astype(float) * 7
        + (out["relative_volume"] >= 1.1).astype(float) * 5
        + (out["kaufman_ratio"] >= min_kaufman_ratio).astype(float) * 8
        + (out["trend_strength"] >= min_trend_strength).astype(float) * 6
        + out["close_near_low"].astype(float) * 5
        + (out["reject_short"] | out["sweep_short"]).astype(float) * 8
        + out["reversal_short"].astype(float) * 9
        + out["break_short"].astype(float) * 6
        - (out["chop_score"] > max_chop_score).astype(float) * 12
        - out["fakeout_short_risk"].astype(float) * 12
        - out["do_not_chase_short"].astype(float) * 10
        - (out["bull_trend"] & out["htf_bull"]).astype(float) * 12
    ).clip(lower=0, upper=100)
    out["chop_ok"] = (
        (out["chop_score"] <= max_chop_score)
        | out["reversal_long"]
        | out["reversal_short"]
    )
    out["long_valid"] = (
        (out["long_score"] >= min_setup_score)
        & (out["long_score"] >= out["short_score"] + 4)
        & ~out["fakeout_long_risk"]
        & ~out["do_not_chase_long"]
        & out["chop_ok"]
        & (out["kaufman_long"] | out["atr_long"] | out["reversal_long"])
    )
    out["short_valid"] = (
        (out["short_score"] >= min_setup_score)
        & (out["short_score"] >= out["long_score"] + 4)
        & ~out["fakeout_short_risk"]
        & ~out["do_not_chase_short"]
        & out["chop_ok"]
        & (out["kaufman_short"] | out["atr_short"] | out["reversal_short"])
    )
    out["signal"] = "WAIT"
    out.loc[out["long_valid"], "signal"] = "BUY"
    out.loc[out["short_valid"], "signal"] = "SELL"
    out["setup"] = "WAIT"
    out.loc[out["reversal_long"] | out["reversal_short"], "setup"] = "Reversal"
    out.loc[out["atr_long"] | out["atr_short"], "setup"] = "ATRExpansion"
    out.loc[out["kaufman_long"] | out["kaufman_short"], "setup"] = "KaufmanRatio"
    return out


def score_algorithm(df: pd.DataFrame) -> pd.DataFrame:
    """Backward-compatible alias used by older scripts."""
    return add_v44_indicators(df)
