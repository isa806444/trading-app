"""Write the AI algorithm strategy to a TradingView Pine Script file."""

from pathlib import Path

from .pinescript import build_pine_script


def main() -> None:
    script = build_pine_script()
    if not script.startswith("//@version=5\n"):
        raise RuntimeError("Pine export must start with //@version=5 on line 1.")

    output = Path("tradingview/ai_algorithm_strategy.pine")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(script, encoding="utf-8", newline="\n")
    paste_output = Path("tradingview/PASTE_THIS_IN_TRADINGVIEW.pine")
    paste_output.write_text(script, encoding="utf-8", newline="\n")
    print(f"Wrote {output}")
    print(f"Wrote {paste_output}")


if __name__ == "__main__":
    main()
