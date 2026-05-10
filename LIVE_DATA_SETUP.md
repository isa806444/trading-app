# Futures Bot Setup

The bot now uses this split:

- TradingView Premium: live futures candles and alerts.
- Databento: historical futures context for verification and backtesting.
- Tradeify/Tradovate: execution using the Tradovate credentials from your Tradeify account.

## Render Environment

Required for TradingView alerts:

```text
TRADINGVIEW_WEBHOOK_SECRET=make_a_private_secret
```

Required for Databento verification:

```text
DATABENTO_API_KEY=your_databento_key
DATABENTO_DATASET=GLBX.MDP3
DATABENTO_SCHEMA=ohlcv-1m
DATABENTO_STYPE_IN=raw_symbol
DATABENTO_SYMBOL_MAP=ES=ESM6,NQ=NQM6,MES=MESM6,MNQ=MNQM6,YM=YMM6,RTY=RTYM6,CL=CLM6,GC=GCM6
DATABENTO_VERIFY_ALERTS_ENABLED=true
DATABENTO_LOOKBACK_MINUTES=240
DATABENTO_MIN_RECORDS=40
DATABENTO_MAX_ALERT_DEVIATION_PCT=1.25
```

Required for Tradeify/Tradovate demo routing:

```text
TRADOVATE_ENV=demo
TRADOVATE_USERNAME=your_tradeify_tradovate_username
TRADOVATE_PASSWORD=your_tradeify_tradovate_password
TRADOVATE_APP_ID=your_tradovate_app_id
TRADOVATE_APP_VERSION=1.0.0
TRADOVATE_CID=your_tradovate_cid
TRADOVATE_SECRET=your_tradovate_secret
TRADOVATE_ACCOUNT_SPEC=your_tradeify_account_name
TRADOVATE_ACCOUNT_ID=your_tradeify_account_id
TRADOVATE_SYMBOL_MAP=ES=ESM6,NQ=NQM6,MES=MESM6,MNQ=MNQM6,YM=YMM6,RTY=RTYM6,CL=CLM6,GC=GCM6
TRADOVATE_AUTO_TRADE_ENABLED=false
TRADOVATE_DEFAULT_ORDER_QTY=1
TRADOVATE_MAX_ORDER_QTY=1
TRADOVATE_MAX_DAILY_ORDERS=5
ALGO_MIN_EDGE_FOR_AUTO_TRADE=18
ALGO_DEFAULT_TARGET_PCT=0.02
ALGO_DEFAULT_STOP_PCT=0.01
```

Update the `M6` contract codes when the active futures month rolls.

Only after demo orders work, live routing also needs:

```text
TRADOVATE_ENV=live
TRADOVATE_LIVE_TRADING_ACK=I_UNDERSTAND_REAL_MONEY_RISK
```

## TradingView

Open this file in TradingView Pine Editor:

```text
tradingview/ai_algorithm_strategy.pine
```

Run it on an NQ futures chart like `NQ1!`. Create an alert using `Any alert() function call` and this webhook URL:

```text
https://trading-app-kb38.onrender.com/tradingview-webhook?secret=make_a_private_secret
```

The Pine alert sends `BUY` or `SELL` plus live price, target, stop, contracts, edge, and bar time. The app is permanently armed to NQ, rejects non-futures symbols, rejects all non-NQ futures alerts, checks Databento, then sends verified bracket orders through Tradeify/Tradovate.

## Databento Backtesting

Run futures backtests locally:

```powershell
pip install -r requirements-research.txt
python -m algo_research.run_backtest --symbol ESM6 --start 2026-05-01T13:30:00Z --end 2026-05-08T20:00:00Z --dataset GLBX.MDP3
```
