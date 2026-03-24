# Trading App

This project is a lightweight trading dashboard built with Flask, vanilla HTML, and real market data.

## Current Features

- Search a ticker and generate a basic trade plan
- Switch between strategy presets: `scalp`, `day`, `swing`, `momentum`, `mean`
- View candlestick charts with timeframe controls
- Save and remove watchlist tickers with persistence in `watchlist.json`
- Manual refresh with live/cache/demo status messaging
- Real market data through Twelve Data when `TWELVE_DATA_API_KEY` is configured
- Demo fallback when no provider key is set or the provider errors

## Project Structure

- `main.py`: Flask backend and API routes
- `static/index.html`: frontend UI
- `watchlist.json`: saved watchlist data
- `market_cache.json`: cached quote and candle data
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

6. Create a `.env` file from `.env.example` and add your Twelve Data API key:

```powershell
copy .env.example .env
```

Then edit `.env` and set:

```text
TWELVE_DATA_API_KEY=your_real_twelve_data_key
STRIPE_PUBLISHABLE_KEY=your_stripe_publishable_key
STRIPE_SECRET_KEY=your_stripe_secret_key
STRIPE_PREMIUM_PRICE_CENTS=999
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
3. Add the environment variable:

```text
TWELVE_DATA_API_KEY=your_real_twelve_data_key
STRIPE_PUBLISHABLE_KEY=your_stripe_publishable_key
STRIPE_SECRET_KEY=your_stripe_secret_key
STRIPE_PREMIUM_PRICE_CENTS=999
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

- Market data now prefers Twelve Data when `TWELVE_DATA_API_KEY` is set.
- Premium billing uses Stripe Checkout and Stripe Customer Portal when the Stripe keys are configured.
- The CBOE SKEW Index uses a separate market-data fetch path.
- If the provider is unavailable, the app falls back to cached data and then demo data.
- The strategy output is still placeholder logic, not real financial advice or broker-connected execution.
- If `python` does not work in PowerShell, try `py` instead.
