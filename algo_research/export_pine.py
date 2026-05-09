"""Write the AI algorithm strategy to a TradingView Pine Script file."""

from pathlib import Path

from .pinescript import build_pine_script


def main() -> None:
    output = Path("tradingview/ai_algorithm_strategy.pine")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_pine_script(), encoding="utf-8")
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
