# Live Bot Setup

The app now uses this split:

- TradingView: live chart data, Pine Script algorithm, and webhook alerts.
- Tradovate: broker execution for webhook alerts.
- Databento: Python, VS Code, and Jupyter backtesting only.

## Render Environment

Required for TradingView alerts:

```text
TRADINGVIEW_WEBHOOK_SECRET=make_a_private_secret
```

Required for Tradovate demo routing:

```text
TRADOVATE_ENV=demo
TRADOVATE_USERNAME=your_tradovate_username
TRADOVATE_PASSWORD=your_tradovate_password
TRADOVATE_APP_ID=your_tradovate_app_id
TRADOVATE_APP_VERSION=1.0.0
TRADOVATE_CID=your_tradovate_cid
TRADOVATE_SECRET=your_tradovate_secret
TRADOVATE_ACCOUNT_SPEC=your_account_name
TRADOVATE_ACCOUNT_ID=your_account_id
TRADOVATE_SYMBOL_MAP=ES=ESM6,NQ=NQM6
TRADOVATE_AUTO_TRADE_ENABLED=false
TRADOVATE_DEFAULT_ORDER_QTY=1
TRADOVATE_MAX_ORDER_QTY=1
TRADOVATE_MAX_DAILY_ORDERS=5
ALGO_MIN_EDGE_FOR_AUTO_TRADE=18
ALGO_DEFAULT_TARGET_PCT=0.02
ALGO_DEFAULT_STOP_PCT=0.01
```

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

Create an alert using `Any alert() function call` and this webhook URL:

```text
https://trading-app-kb38.onrender.com/tradingview-webhook?secret=make_a_private_secret
```

The Pine alert sends `BUY` or `SELL` plus live price, target, stop, contracts, edge, and bar time. The app places an OSO bracket order in Tradovate so the target and stop are broker-side.

## Databento Backtesting

Databento is not used for live signals. Run it locally:

```powershell
pip install -r requirements-research.txt
python -m algo_research.run_backtest --symbol AAPL --start 2026-05-01T13:30:00Z --end 2026-05-08T20:00:00Z
```
