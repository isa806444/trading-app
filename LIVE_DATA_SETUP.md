# Live Data Setup

The app now uses this split:

- TradingView: charting, Pine Script strategy, and webhook alerts into the app.
- Tradovate: live futures market data through the Market Data WebSocket API.
- Databento: Python, VS Code, and Jupyter backtesting only.

## Render Environment Variables

Add these for Tradovate live data:

```text
TRADOVATE_ENV=demo
TRADOVATE_USERNAME=your_tradovate_username
TRADOVATE_PASSWORD=your_tradovate_password
TRADOVATE_APP_ID=your_tradovate_app_id
TRADOVATE_APP_VERSION=1.0.0
TRADOVATE_CID=your_tradovate_cid
TRADOVATE_SECRET=your_tradovate_secret
TRADOVATE_SYMBOL_MAP=ES=ESM6,NQ=NQM6
```

Use `TRADOVATE_ENV=live` only after demo mode is working and the account/risk controls are ready.

Add this for TradingView webhooks:

```text
TRADINGVIEW_WEBHOOK_SECRET=make_a_private_secret
```

Then set your TradingView alert webhook URL to:

```text
https://trading-app-kb38.onrender.com/tradingview-webhook?secret=make_a_private_secret
```

## TradingView

Open the Pine Script from:

```text
tradingview/ai_algorithm_strategy.pine
```

Paste it into TradingView Pine Editor, add it to a chart, then create an alert using the strategy's alert function or order-fill events.

## Databento Backtesting

Databento is intentionally not used by the live Flask app. Use it locally in Python/Jupyter:

```powershell
pip install -r requirements-research.txt
python -m algo_research.run_backtest --symbol AAPL --start 2026-05-01T13:30:00Z --end 2026-05-08T20:00:00Z
```
