# NQ1 Paper Bot Setup

The bot now uses this split:

- TradingView Premium: live NQ1 strategy candles and alerts.
- Databento: optional historical research/backtesting.
- Optional Lucid/Tradovate bridge: NQ1 alerts can route to the active NQ futures contract.

## Render Environment

Required for TradingView alerts:

```text
TRADINGVIEW_WEBHOOK_SECRET=make_a_private_secret
```

Optional for Databento research:

```text
DATABENTO_API_KEY=your_databento_key
DATABENTO_DATASET=GLBX.MDP3
DATABENTO_SCHEMA=ohlcv-1m
DATABENTO_STYPE_IN=raw_symbol
DATABENTO_SYMBOL_MAP=ES=ESM6,NQ=NQM6,MES=MESM6,YM=YMM6,RTY=RTYM6,CL=CLM6,GC=GCM6
DATABENTO_VERIFY_ALERTS_ENABLED=true
DATABENTO_LOOKBACK_MINUTES=240
DATABENTO_MIN_RECORDS=40
DATABENTO_MAX_ALERT_DEVIATION_PCT=1.25
```

Optional for Lucid/Tradovate demo routing:

```text
TRADOVATE_ENV=demo
TRADOVATE_USERNAME=your_lucid_tradovate_username
TRADOVATE_PASSWORD=your_lucid_tradovate_password
TRADOVATE_APP_ID=your_tradovate_app_id
TRADOVATE_APP_VERSION=1.0.0
TRADOVATE_CID=your_tradovate_cid
TRADOVATE_SECRET=your_tradovate_secret
TRADOVATE_ACCOUNT_SPEC=your_lucid_account_name
TRADOVATE_ACCOUNT_ID=your_lucid_account_id
TRADOVATE_SYMBOL_MAP=ES=ESM6,NQ=NQM6,MES=MESM6,YM=YMM6,RTY=RTYM6,CL=CLM6,GC=GCM6
TRADOVATE_AUTO_TRADE_ENABLED=false
TRADOVATE_NQ_BRIDGE_ENABLED=false
TRADOVATE_NQ_EXECUTION_SYMBOL=NQM6
TRADOVATE_TICK_SIZE=0.25
TRADOVATE_DEFAULT_ORDER_QTY=1
TRADOVATE_MAX_ORDER_QTY=1
TRADOVATE_MAX_DAILY_ORDERS=5
TRADOVATE_MAX_ACCOUNT_SIZE_USD=100000
TRADOVATE_NQ_DOLLARS_PER_POINT=20
ALGO_MIN_EDGE_FOR_AUTO_TRADE=18
ALGO_DEFAULT_TARGET_PCT=0.02
ALGO_DEFAULT_STOP_PCT=0.01
```

NQ1 is the TradingView continuous ticker. Tradovate routes to the active NQ futures contract, such as `NQM6`. Update the contract code when the active futures month rolls.

Only after futures demo orders work, live routing also needs:

```text
TRADOVATE_ENV=live
TRADOVATE_LIVE_TRADING_ACK=I_UNDERSTAND_REAL_MONEY_RISK
```

## TradingView

Open this copy-ready file in TradingView Pine Editor:

```text
tradingview/PASTE_THIS_IN_TRADINGVIEW.pine
```

That file now exports the clean v44 routing shell. The old trade logic has been removed so the next strategy brain can start fresh.

Use `Ctrl+A` in TradingView first, delete the old script, then paste the full file. The very first line must be exactly `//@version=6`.

Run it on the TradingView NQ continuous chart `NQ1!` or the active NQ contract. Keep the chart set to `5m`.

The v44 shell keeps only the routing guardrails:

- It uses the tag `nq1-smart-paper-v44`; set `TRADINGVIEW_ALLOWED_TAGS=nq1-smart-paper-v44` on Render.
- It locks to NQ1/NQ charts and rejects MNQ charts.
- It locks to `5m` by default.
- It uses `SMART_EXIT_ONLY` routing with no fixed profit target.
- It keeps `strategy.entry`, `strategy.close`, and TradingView alert JSON ready for Render/app/Tradovate.
- It has no active indicators, entries, exits, filters, scoring, or trade logic.

Note: Pine Script cannot rewrite its own source code, run an external machine-learning model inside TradingView, safely look into future bars, or see real broker order flow/other traders' live positions unless that data is provided on the chart.

Create an alert using `Any alert() function call` and this webhook URL:

```text
https://trading-app-kb38.onrender.com/tradingview-webhook?secret=make_a_private_secret
```

The Pine alert sends `BUY` or `SELL` plus live price, stop mode, optional stop, contracts, edge, score, AI score, AI bias, entry style, buyer/seller pressure, pattern, projection, decision zone, crowd-pressure proxy, timeframe, profit mode, reason, and bar time. The v44 bot sends `profit_mode=TRAIL_ONLY`, `target=null`, and defaults to `stop_mode=SMART_EXIT_ONLY`.

Live intrabar entries only trigger on realtime forming candles. Historical 5m candles still may not contain the exact tick path, so old candles may show bar-close-style entries unless you test on a lower timeframe or use TradingView Bar Magnifier.

In `SMART_EXIT_ONLY` mode, the app routes entries as plain market orders and waits for TradingView `EXIT_LONG` or `EXIT_SHORT` alerts to close them. If you turn `Use Hard Protective Stop` back on, the app routes bracket orders again and requires a stop.

Safe demo sequence:

1. Keep `TRADOVATE_ENV=demo`.
2. Set `TRADOVATE_NQ_EXECUTION_SYMBOL=NQM6` or the active NQ contract.
3. Set `TRADOVATE_NQ_BRIDGE_ENABLED=true`.
4. Set `TRADOVATE_AUTO_TRADE_ENABLED=true`.
5. Keep `TRADOVATE_MAX_ORDER_QTY=1` and `TRADOVATE_MAX_DAILY_ORDERS=5`.

## Databento Backtesting

Run futures-style Databento backtests locally:

```powershell
pip install -r requirements-research.txt
python -m algo_research.run_backtest --symbol NQM6 --start 2026-05-01T13:30:00Z --end 2026-05-08T20:00:00Z --dataset GLBX.MDP3
```

Backtests support overfitting checks:

```powershell
python -m algo_research.run_backtest --symbol NQM6 --start 2026-05-01T13:30:00Z --end 2026-05-08T20:00:00Z --dataset GLBX.MDP3 --slippage-pct 0.0001 --fee-per-trade 4 --validation-fraction 0.30 --monte-carlo-runs 500
```

The summary includes in-sample results, out-of-sample results, and Monte Carlo trade-resampling stats.
