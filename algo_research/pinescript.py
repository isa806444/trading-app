"""TradingView Pine Script exporter for the active NQ Quantum Pro strategy."""

from __future__ import annotations

from pathlib import Path


def build_pine_script() -> str:
    """Return the current main bot Pine source used by TradingView."""
    root = Path(__file__).resolve().parents[1]
    pine_path = root / "tradingview" / "separate_pine_files" / "NQ_QUANTUM_PRO_AI_STRATEGY.pine"
    return pine_path.read_text(encoding="utf-8")
