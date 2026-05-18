"""Write the active AI algorithm strategy to TradingView Pine Script files."""

from pathlib import Path

from .pinescript import build_pine_script as build_active_v44_script


def main() -> None:
    script = build_active_v44_script()
    if not script.startswith("//@version=6\n"):
        raise RuntimeError("Pine export must start with //@version=6 on line 1.")

    output = Path("tradingview/ai_algorithm_strategy.pine")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(script, encoding="utf-8", newline="\n")
    paste_output = Path("tradingview/PASTE_THIS_IN_TRADINGVIEW.pine")
    paste_output.write_text(script, encoding="utf-8", newline="\n")
    bot_output = Path("tradingview/nq_ai_trader_v44.pine")
    bot_output.write_text(script, encoding="utf-8", newline="\n")
    bot_paste_output = Path("tradingview/PASTE_THIS_V44_BOT_IN_TRADINGVIEW.pine")
    bot_paste_output.write_text(script, encoding="utf-8", newline="\n")
    parked_output = Path("tradingview/PARKED_V44_BOT.pine")
    parked_output.write_text(script, encoding="utf-8", newline="\n")
    print(f"Wrote {output}")
    print(f"Wrote {paste_output}")
    print(f"Wrote {bot_output}")
    print(f"Wrote {bot_paste_output}")
    print(f"Wrote {parked_output}")


if __name__ == "__main__":
    main()
