# NQ Quantum Pro AI Strategy - Build And Test Notes

Generated: 2026-05-21

## Strategy file

`NQ_QUANTUM_PRO_AI_STRATEGY.pine`

This is a brand-new standalone NQ futures strategy with 100k simulated Strategy Report capital.

## What was built

- Strict no-repaint defaults: bar-close logic, `calc_on_every_tick=false`, `process_orders_on_close=true`, and `barmerge.lookahead_off`.
- NQ-only lock so it does not accidentally fire on the wrong symbol.
- Rolling 4-year backtest window input.
- Regime engine: trend, range, compression, high-volatility, and shock-candle detection.
- Cross-market brain: ES, QQQ, VIX, DXY, US10Y, NVDA, AAPL, MSFT, and SMH.
- Higher-timeframe brain: 15m, 1h, 4h, and daily confirmed context.
- Pattern brain: VWAP reclaim/rejection, liquidity sweeps, prior-day sweeps, pin bars, engulfing candles, trend pullbacks, compression breakouts, and FVG retests.
- Adaptive learning: after closed trades, the script raises or lowers penalties by direction, setup type, and hour-of-day.
- Risk controls: max daily loss, max trades, max losses, max consecutive losses, profit lock, cooldown after losses, and big-win cooldown.
- Smart exits: trailing stop, breakeven, profit giveback protection, thesis-fail exit, bias-flip exit, shock exit, and max-bars-in-trade exit.
- Strategy Report trades: `strategy.entry`, `strategy.exit`, and `strategy.close` are wired for simulated backtesting.
- Alerts: dynamic alert payloads include setup, score, stop, target, regime, uncertainty, cross score, and HTF score.

## Databento Python backtest

Ran locally from Databento `GLBX.MDP3` continuous `NQ.c.0` 1H OHLCV bars.

- Window: 2022-05-21 16:00 UTC to 2026-05-21 16:00 UTC
- Starting simulated cash: $100,000
- Contracts: 1 NQ
- Slippage: 0.5 points
- Commission: $4 per contract side
- Total trades: 1,181
- Total P&L: +$122,128.30
- Ending simulated cash: $222,128.30
- Win rate: 42.08%
- Profit factor: 1.583
- Max drawdown: -$18,573.00 (-8.79%)
- Largest win: +$8,672.00
- Largest loss: -$413.00
- Expectancy: +$103.41 per trade

## Forward-test proxy

The Python report uses the newest 30% of closed trades as the out-of-sample / forward-test proxy.

- Out-of-sample trades: 355
- Out-of-sample P&L: +$55,885.00
- Out-of-sample win rate: 42.82%
- Out-of-sample profit factor: 1.862
- Out-of-sample expectancy: +$157.42 per trade

This is still a historical out-of-sample split, not live forward testing. True live forward testing starts once TradingView alerts fire into Render and the app logs them.

## How to test it now

1. Open TradingView.
2. Use `NQ1!` or your active NQ continuous futures chart.
3. Paste `NQ_QUANTUM_PRO_AI_STRATEGY.pine` into Pine Editor.
4. Add it to chart as a strategy.
5. Open Strategy Tester.
6. Start on a 15m or 1h chart first, then test 5m if you want faster reactions.
7. Leave `Strict No-Repaint Bar Close` on.
8. Leave the rolling backtest window at 4 years.

## Live forward test plan

- Run it on paper only for at least 1 week.
- Compare 5m, 15m, and 1h results.
- Do not optimize one setting until it looks perfect on one week only.
- Track net profit, max drawdown, profit factor, total trades, win rate, average win, average loss, and largest loss.
- If it overtrades, raise `Minimum Setup Score` or `Minimum Confirmation Votes`.
- If it misses too many good trades, lower `Minimum Setup Score` slightly before touching risk settings.

## Important safety note

This is not guaranteed profitable. It is a serious no-repaint strategy framework designed to be tested honestly.

The next step is to run it live on paper from TradingView alerts and compare live fills against the Python/app report.
