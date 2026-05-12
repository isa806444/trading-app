# Trading App

This project is an MNQ-focused AI algorithm paper-signal dashboard built with Flask, TradingView alerts, Tradeify/Tradovate routing hooks, and Databento research hooks.

## Current Features

- Run the MNQ AI algorithm board
- Keep the TradingView Pine Script strategy private in the repo for the Micro E-mini Nasdaq 100 1-hour bot
- Receive TradingView webhook alerts from the live MNQ strategy chart
- Keep the bot permanently armed to MNQ so all other alerts are ignored
- Log MNQ TradingView alerts as app paper signals
- Optionally route MNQ alerts to Tradeify/Tradovate using the active MNQ futures contract
- Save and remove watchlist tickers with persistence in `watchlist.json`
- Backtest with Databento in Python, VS Code, and Jupyter

## Project Structure

- `main.py`: Flask backend and API routes
- `static/index.html`: frontend UI
- `watchlist.json`: saved watchlist data
- `market_cache.json`: cached quote and candle data
- `tradingview/ai_algorithm_strategy.pine`: TradingView live alert strategy
- `algo_research/`: Databento backtesting tools
- `.env.example`: environment variable template for API keys
- `requirements.txt`: Python dependencies

## Setup

1. Make sure Python 3.10+ is installed.
2. Open a terminal in `C:\Users\donov\OneDrive\Desktop\trading-app`.
3. Create a virtual environment:

```powershell
python -m venv .venv
```

4. Activate it:

```powershell
.\.venv\Scripts\Activate.ps1
```

5. Install dependencies:

```powershell
pip install -r requirements.txt
```

6. Create a `.env` file from `.env.example` and add your keys:

```powershell
copy .env.example .env
```

For the MNQ paper bot, start with:

```text
TRADINGVIEW_WEBHOOK_SECRET=make_a_private_secret
```

For Tradeify/Tradovate demo routing, add your Tradovate credentials and keep the bridge guarded:

```text
TRADOVATE_ENV=demo
TRADOVATE_MNQ_BRIDGE_ENABLED=true
TRADOVATE_MNQ_EXECUTION_SYMBOL=MNQM6
TRADOVATE_AUTO_TRADE_ENABLED=true
TRADOVATE_MAX_ORDER_QTY=1
TRADOVATE_MAX_DAILY_ORDERS=5
```

## Run

Start the Flask app:

```powershell
python main.py
```

Then open:

- `http://127.0.0.1:5000`

## Deploy To Render

This project is set up for Render with `render.yaml`.

1. Push this folder to a GitHub repository.
2. In Render, create a new `Blueprint` deployment and select that repo.
3. Add the environment variables from `.env.example`.

TradingView webhook URL:

```text
https://trading-app-kb38.onrender.com/tradingview-webhook?secret=make_a_private_secret
```

4. Deploy the service.
5. Open the Render URL from your phone browser.

Render will run the app with:

```text
gunicorn main:app
```

## Important Hosting Note

- `watchlist.json` and `market_cache.json` are file-based.
- On a basic cloud web service, those files are not guaranteed to persist forever across rebuilds or instance replacement.
- That means your watchlist and cache may reset after redeploys or infrastructure restarts.
- If you want permanent cloud persistence later, the next step is moving watchlist/cache storage into a database.

## Notes

- TradingView is the live signal source for the MNQ paper bot.
- Tradeify/Tradovate powers routed execution when the bridge variables are enabled.
- Databento can still power research/backtesting.
- Pine strategies cannot directly auto-fill TradingView's built-in Paper Trading panel; use TradingView Strategy Tester, app paper-signal tracking, or the guarded Tradeify/Tradovate webhook bridge.
- The app is locked to MNQ and rejects non-MNQ TradingView alerts.
- The included Pine strategy is designed for paper testing on an MNQ chart such as `MNQ1!` or the active MNQ contract using the 1-hour timeframe.
- If `python` does not work in PowerShell, try `py` instead.
