# Trading App

This project is a futures-only AI algorithm trading dashboard built with Flask, TradingView alerts, Databento verification/backtesting, and Tradeify/Tradovate execution hooks.

## Current Features

- Run the NQ futures AI algorithm board
- Export the TradingView Pine Script strategy
- Receive TradingView webhook alerts from live futures charts
- Keep the bot permanently armed to NQ so all other futures alerts are ignored
- Verify entry alerts against Databento historical futures data
- Route verified alerts to Tradeify/Tradovate OSO bracket orders when auto-trading is enabled
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

For live bot routing, start with demo mode:

```text
TRADINGVIEW_WEBHOOK_SECRET=make_a_private_secret
DATABENTO_API_KEY=your_databento_key
DATABENTO_DATASET=GLBX.MDP3
DATABENTO_SCHEMA=ohlcv-1m
DATABENTO_VERIFY_ALERTS_ENABLED=true
TRADOVATE_ENV=demo
TRADOVATE_USERNAME=your_tradeify_tradovate_username
TRADOVATE_PASSWORD=your_tradeify_tradovate_password
TRADOVATE_APP_ID=your_tradovate_app_id
TRADOVATE_CID=your_tradovate_cid
TRADOVATE_SECRET=your_tradovate_secret
TRADOVATE_ACCOUNT_SPEC=your_tradeify_account_name
TRADOVATE_ACCOUNT_ID=your_tradeify_account_id
TRADOVATE_AUTO_TRADE_ENABLED=false
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

- TradingView is the live signal source for futures bots.
- Databento verifies entry alerts and powers research/backtesting.
- Tradeify execution uses the Tradovate credentials provided by your Tradeify account.
- Tradeify/Tradovate execution is locked until `TRADOVATE_AUTO_TRADE_ENABLED=true`.
- Live orders also require `TRADOVATE_LIVE_TRADING_ACK=I_UNDERSTAND_REAL_MONEY_RISK`.
- If `python` does not work in PowerShell, try `py` instead.
