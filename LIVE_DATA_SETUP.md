# NDX Paper Bot Setup

The bot now uses this split:

- TradingView Premium: live NDX strategy candles and alerts.
- Polygon: NDX index quote display in the app using `I:NDX`.
- Databento: optional historical research/backtesting.
- Optional Tradeify/Tradovate bridge: NDX alerts can map to a tradable NQ/MNQ futures contract.

## Render Environment

Required for TradingView alerts:

```text
TRADINGVIEW_WEBHOOK_SECRET=make_a_private_secret
```

Required for Polygon NDX quotes:

```text
POLYGON_API_KEY=your_polygon_key
```

Optional for Databento research:

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

Optional for Tradeify/Tradovate demo routing:

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
TRADOVATE_NDX_BRIDGE_ENABLED=false
TRADOVATE_NDX_EXECUTION_SYMBOL=MNQM6
TRADOVATE_TICK_SIZE=0.25
TRADOVATE_DEFAULT_ORDER_QTY=1
TRADOVATE_MAX_ORDER_QTY=1
TRADOVATE_MAX_DAILY_ORDERS=5
ALGO_MIN_EDGE_FOR_AUTO_TRADE=18
ALGO_DEFAULT_TARGET_PCT=0.02
ALGO_DEFAULT_STOP_PCT=0.01
```

`NDX` is not directly tradable. The bridge maps NDX alerts to the contract in `TRADOVATE_NDX_EXECUTION_SYMBOL`, usually `MNQ`/`NQ` with the active month code such as `MNQM6` or `NQM6`. Update the `M6` contract codes when the active futures month rolls.

Only after futures demo orders work, live routing also needs:

```text
TRADOVATE_ENV=live
TRADOVATE_LIVE_TRADING_ACK=I_UNDERSTAND_REAL_MONEY_RISK
```

## TradingView

Open this file in TradingView Pine Editor:

```text
tradingview/ai_algorithm_strategy.pine
```

Run it on a Nasdaq 100 chart like `NDX`. Keep the chart set to `1H`. The strategy is intentionally single-timeframe and only fires confirmed bar-close alerts, so it does not use multi-timeframe lookups or repainting live-bar signals.

The Pine strategy has guardrails built in:

- Maximum of 5 entries per day
- Hard stop-loss on every trade
- Full-position take-profit on every trade
- ATR and structure-based stop sizing
- Breakeven/trailing stop protection after the trade proves itself
- Smart early-exit alerts when trend, VWAP, DI, RSI, or volatility conditions break
- Symbol guard for NDX charts

Create an alert using `Any alert() function call` and this webhook URL:

```text
https://trading-app-kb38.onrender.com/tradingview-webhook?secret=make_a_private_secret
```

The Pine alert sends `BUY` or `SELL` plus live price, targets, stop, contracts, edge, score, timeframe, reason, and bar time.

Important: Pine strategies simulate fills inside TradingView's broker emulator and can trigger alerts. Pine does not directly take over the TradingView Paper Trading panel by itself. For automated paper execution outside the Strategy Tester, this app receives TradingView webhooks and can route them to Tradeify/Tradovate when all bridge env vars are enabled.

Safe demo sequence:

1. Keep `TRADOVATE_ENV=demo`.
2. Set `TRADOVATE_NDX_EXECUTION_SYMBOL=MNQM6` or the active MNQ contract.
3. Set `TRADOVATE_NDX_BRIDGE_ENABLED=true`.
4. Set `TRADOVATE_AUTO_TRADE_ENABLED=true`.
5. Keep `TRADOVATE_MAX_ORDER_QTY=1` and `TRADOVATE_MAX_DAILY_ORDERS=5`.

## Databento Backtesting

Run futures-style Databento backtests locally:

```powershell
pip install -r requirements-research.txt
python -m algo_research.run_backtest --symbol ESM6 --start 2026-05-01T13:30:00Z --end 2026-05-08T20:00:00Z --dataset GLBX.MDP3
```
