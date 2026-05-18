"""Databento data loader for VS Code and Jupyter research."""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


DEFAULT_DATASET = os.getenv("DATABENTO_DATASET", "GLBX.MDP3")
DEFAULT_SCHEMA = os.getenv("DATABENTO_SCHEMA", "ohlcv-1m")


def load_local_env(path: str = ".env") -> None:
    """Load simple KEY=VALUE pairs without printing secrets or adding a dependency."""
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _require_databento():
    try:
        import databento as db
    except ImportError as exc:
        raise RuntimeError("Install research requirements first: pip install -r requirements-research.txt") from exc
    return db


def fetch_ohlcv(
    symbol: str,
    start: str | None = None,
    end: str | None = None,
    dataset: str = DEFAULT_DATASET,
    schema: str = DEFAULT_SCHEMA,
    stype_in: str = "raw_symbol",
) -> pd.DataFrame:
    """Fetch Databento OHLCV bars and return a clean pandas DataFrame."""
    load_local_env()
    db = _require_databento()
    end_dt = pd.Timestamp(end) if end else pd.Timestamp(datetime.now(timezone.utc))
    start_dt = pd.Timestamp(start) if start else end_dt - pd.Timedelta(days=10)

    api_key = os.getenv("DATABENTO_API_KEY")
    client = db.Historical(api_key) if api_key else db.Historical()
    data = client.timeseries.get_range(
        dataset=dataset,
        schema=schema,
        symbols=[symbol.upper()],
        stype_in=stype_in,
        start=start_dt.isoformat(),
        end=end_dt.isoformat(),
    )

    frame = data.to_df().reset_index()
    if "symbol" in frame.columns:
        frame = frame[frame["symbol"].astype(str).str.upper() == symbol.upper()]

    time_col = next((col for col in ["ts_event", "ts_recv", "time", "index"] if col in frame.columns), None)
    if time_col is None:
        raise ValueError("Databento response did not include a timestamp column.")

    out = frame.rename(columns={time_col: "time"}).copy()
    out["time"] = pd.to_datetime(out["time"], utc=True)
    keep = ["time", "open", "high", "low", "close", "volume"]
    missing = [col for col in keep if col not in out.columns]
    if missing:
        raise ValueError(f"Databento response is missing columns: {missing}")

    out = out[keep].dropna().sort_values("time").reset_index(drop=True)
    numeric_cols = ["open", "high", "low", "close", "volume"]
    out[numeric_cols] = out[numeric_cols].apply(pd.to_numeric, errors="coerce")
    return out.dropna().reset_index(drop=True)


def save_ohlcv_csv(df: pd.DataFrame, symbol: str, output_dir: str = "data/databento") -> Path:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    file_path = path / f"{symbol.upper()}_ohlcv.csv"
    df.to_csv(file_path, index=False)
    return file_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Databento OHLCV bars for backtesting.")
    parser.add_argument("--symbol", default="ESM6")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    parser.add_argument("--output-dir", default="data/databento")
    args = parser.parse_args()

    frame = fetch_ohlcv(args.symbol, args.start, args.end, args.dataset, args.schema)
    file_path = save_ohlcv_csv(frame, args.symbol, args.output_dir)
    print(f"Saved {len(frame)} rows to {file_path}")


if __name__ == "__main__":
    main()
