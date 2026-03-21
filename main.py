from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import hashlib
import json
import math
import os
import requests
import time

app = Flask(__name__, static_folder="static")
CORS(app)

WATCHLIST_FILE = "watchlist.json"
MARKET_CACHE_FILE = "market_cache.json"
TWELVE_DATA_BASE_URL = "https://api.twelvedata.com"
TWELVE_DATA_API_KEY_ENV = "TWELVE_DATA_API_KEY"
QUOTE_CACHE_TTL = 60
CANDLE_CACHE_TTL = 120
INDICATOR_CACHE_TTL = 900
DEMO_TIMEZONE = ZoneInfo("America/New_York")
SKEW_SYMBOL = "^SKEW"
SKEW_CACHE_KEY = "indicator:skew"

quote_cache = {}
candle_cache = {}


# =========================
# CONFIG
# =========================

def load_env_file():
    env_path = ".env"
    if not os.path.exists(env_path):
        return

    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        return


load_env_file()


def get_twelve_data_api_key():
    return os.environ.get(TWELVE_DATA_API_KEY_ENV, "").strip()


# =========================
# STORAGE
# =========================

def load_watchlist():
    if not os.path.exists(WATCHLIST_FILE):
        return []
    try:
        with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_watchlist(data):
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_market_cache():
    if not os.path.exists(MARKET_CACHE_FILE):
        return {"quotes": {}, "candles": {}}

    try:
        with open(MARKET_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            quotes = data.get("quotes", {})
            candles = data.get("candles", {})
            return {
                "quotes": quotes if isinstance(quotes, dict) else {},
                "candles": candles if isinstance(candles, dict) else {}
            }
    except (json.JSONDecodeError, OSError):
        return {"quotes": {}, "candles": {}}


def save_market_cache():
    with open(MARKET_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "quotes": quote_cache,
            "candles": candle_cache
        }, f, indent=2)


def get_cache_entry(cache, key, ttl):
    entry = cache.get(key)
    if not entry:
        return None

    age = time.time() - entry["timestamp"]
    return {
        "data": entry["data"],
        "stale": age > ttl,
        "age_seconds": round(age, 1)
    }


def set_cache_entry(cache, key, data):
    cache[key] = {
        "data": data,
        "timestamp": time.time()
    }
    save_market_cache()


def initialize_market_cache():
    global quote_cache, candle_cache
    stored = load_market_cache()
    quote_cache = stored["quotes"]
    candle_cache = stored["candles"]


initialize_market_cache()


# =========================
# MARKET DATA
# =========================

def get_timeframe_config(tf):
    return {
        "1m": {"interval": "1min", "points": 390, "step": 60},
        "5m": {"interval": "5min", "points": 90, "step": 300},
        "15m": {"interval": "15min", "points": 90, "step": 900},
        "1d": {"interval": "1day", "points": 30, "step": 86400},
    }.get(tf, {"interval": "5min", "points": 90, "step": 300})


def parse_twelve_timestamp(raw_value):
    if len(raw_value) == 10:
        dt = datetime.fromisoformat(raw_value)
    else:
        dt = datetime.fromisoformat(raw_value.replace(" ", "T"))
    return int(dt.replace(tzinfo=DEMO_TIMEZONE).timestamp())


def get_et_session_key(unix_seconds):
    dt = datetime.fromtimestamp(unix_seconds, tz=DEMO_TIMEZONE)
    return dt.strftime("%Y-%m-%d")


def build_quote_from_candles(candles):
    return {
        "price": round(candles[-1]["close"], 2),
        "open": round(candles[0]["open"], 2),
        "high": round(max(c["high"] for c in candles), 2),
        "low": round(min(c["low"] for c in candles), 2)
    }


def get_quote_from_candles(symbol, preferred_source="cache"):
    cached = get_cache_entry(candle_cache, f"{symbol}:5m", CANDLE_CACHE_TTL)
    if not cached or not cached["data"]:
        return None

    quote_data = build_quote_from_candles(cached["data"])
    set_cache_entry(quote_cache, symbol, quote_data)
    return {
        "data": quote_data,
        "source": preferred_source,
        "cached": preferred_source != "live",
        "stale": cached["stale"],
        "age_seconds": cached["age_seconds"]
    }


def fetch_twelve_data_candles(symbol, tf):
    api_key = get_twelve_data_api_key()
    if not api_key:
        return None

    config = get_timeframe_config(tf)
    response = requests.get(
        f"{TWELVE_DATA_BASE_URL}/time_series",
        params={
            "apikey": api_key,
            "symbol": symbol,
            "interval": config["interval"],
            "outputsize": config["points"],
            "order": "asc",
            "timezone": "America/New_York",
            "format": "JSON",
        },
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()

    if payload.get("status") == "error":
        raise ValueError(payload.get("message", "Unknown Twelve Data error"))

    values = payload.get("values") or []
    if not values:
        return None

    candles = []
    for row in values:
        open_price = float(row["open"])
        close_price = float(row["close"])
        high_price = max(float(row["high"]), open_price, close_price)
        low_price = min(float(row["low"]), open_price, close_price)
        candles.append({
            "time": parse_twelve_timestamp(row["datetime"]),
            "open": round(open_price, 2),
            "high": round(high_price, 2),
            "low": round(low_price, 2),
            "close": round(close_price, 2),
            "volume": float(row.get("volume") or 0),
        })

    return candles


def fetch_and_cache_candles(symbol, tf):
    cache_key = f"{symbol}:{tf}"
    cached_candles = get_cache_entry(candle_cache, cache_key, CANDLE_CACHE_TTL)
    if cached_candles and not cached_candles["stale"]:
        return {
            "candles": cached_candles["data"],
            "source": "cache",
            "cached": True,
            "stale": False,
            "age_seconds": cached_candles["age_seconds"]
        }

    try:
        candle_rows = fetch_twelve_data_candles(symbol, tf)
        if not candle_rows:
            if cached_candles:
                return {
                    "candles": cached_candles["data"],
                    "source": "cache",
                    "cached": True,
                    "stale": True,
                    "age_seconds": cached_candles["age_seconds"]
                }
            return None

        set_cache_entry(candle_cache, cache_key, candle_rows)
        if tf == "5m":
            set_cache_entry(quote_cache, symbol, build_quote_from_candles(candle_rows))

        return {
            "candles": candle_rows,
            "source": "live",
            "cached": False,
            "stale": False,
            "age_seconds": 0
        }
    except Exception as exc:
        print("Twelve Data candle fetch error:", exc)
        if cached_candles:
            return {
                "candles": cached_candles["data"],
                "source": "cache",
                "cached": True,
                "stale": True,
                "age_seconds": cached_candles["age_seconds"]
            }
        return None


def prepare_chart_candles(candles, tf):
    if not candles:
        return candles

    if tf in {"1m", "5m", "15m"}:
        latest_session = get_et_session_key(candles[-1]["time"])
        session_candles = [c for c in candles if get_et_session_key(c["time"]) == latest_session]
        return session_candles or candles

    if tf == "1d":
        return candles[-30:]

    return candles


def get_demo_seed(symbol):
    digest = hashlib.sha256(symbol.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def build_demo_candles(symbol, tf):
    config = get_timeframe_config(tf)
    seed = get_demo_seed(f"{symbol}:{tf}")
    base = 40 + (seed % 260)
    trend = ((seed % 21) - 10) / 1000
    amplitude = 1.2 + ((seed >> 3) % 30) / 10
    volume_base = 500000 + (seed % 1500000)
    now = int(time.time())
    start = now - (config["points"] * config["step"])
    candles = []
    last_close = float(base)

    for idx in range(config["points"]):
        wave = math.sin((idx + (seed % 7)) / 5) * amplitude
        drift = idx * trend * base
        open_price = last_close
        close_price = max(1.0, base + wave + drift)
        high = max(open_price, close_price) + 0.35 + abs(math.cos(idx / 4)) * 0.9
        low = min(open_price, close_price) - 0.35 - abs(math.sin(idx / 4)) * 0.9
        candles.append({
            "time": start + ((idx + 1) * config["step"]),
            "open": round(open_price, 2),
            "high": round(max(high, open_price, close_price), 2),
            "low": round(min(low, open_price, close_price), 2),
            "close": round(close_price, 2),
            "volume": float(int(volume_base + abs(math.sin(idx)) * 150000))
        })
        last_close = close_price

    return candles


def get_demo_market(symbol, tf):
    candles = build_demo_candles(symbol, tf)
    quote_data = build_quote_from_candles(candles)
    return {
        "quote": {
            "data": quote_data,
            "source": "demo",
            "cached": True,
            "stale": False,
            "age_seconds": 0
        },
        "candles": {
            "candles": candles,
            "source": "demo",
            "cached": True,
            "stale": False,
            "age_seconds": 0
        }
    }


def get_data(symbol):
    cached = get_cache_entry(quote_cache, symbol, QUOTE_CACHE_TTL)
    if cached and not cached["stale"]:
        return {
            "data": cached["data"],
            "source": "cache",
            "cached": True,
            "stale": False,
            "age_seconds": cached["age_seconds"]
        }

    candle_backed_quote = get_quote_from_candles(symbol)
    if candle_backed_quote and not candle_backed_quote["stale"]:
        return candle_backed_quote

    candles = fetch_and_cache_candles(symbol, "5m")
    if candles and candles["candles"]:
        refreshed_quote = get_quote_from_candles(symbol, preferred_source=candles["source"])
        if refreshed_quote:
            return refreshed_quote

    if candle_backed_quote:
        return candle_backed_quote

    if cached:
        return {
            "data": cached["data"],
            "source": "cache",
            "cached": True,
            "stale": True,
            "age_seconds": cached["age_seconds"]
        }

    return get_demo_market(symbol, "5m")["quote"]


def get_skew_signal(value):
    if value >= 150:
        return "Elevated tail-risk pricing"
    if value >= 135:
        return "Moderate tail-risk pricing"
    return "Calmer tail-risk pricing"


def build_trade_signal(change, bias, strategy, skew_value=None):
    if change >= 1.0:
        action = "BUY"
        tone = "bullish"
        reason = f"{strategy.title()} strength is positive and price is leading the session."
    elif change <= -1.0:
        action = "SELL"
        tone = "bearish"
        reason = f"{strategy.title()} weakness is in control and price is trailing the session."
    elif bias == "Bullish":
        action = "BUY"
        tone = "bullish"
        reason = "Bias is bullish, but the move is still moderate."
    elif bias == "Bearish":
        action = "SELL"
        tone = "bearish"
        reason = "Bias is bearish, but the move is still moderate."
    else:
        action = "WAIT"
        tone = "neutral"
        reason = "Price is flat versus the open, so there is no clean edge yet."

    if skew_value is not None and skew_value >= 150 and action != "WAIT":
        reason = f"{reason} CBOE SKEW is elevated, so risk should stay tighter than usual."

    return {
        "action": action,
        "tone": tone,
        "reason": reason
    }


def fetch_skew_indicator():
    cached = get_cache_entry(quote_cache, SKEW_CACHE_KEY, INDICATOR_CACHE_TTL)
    if cached and not cached["stale"]:
        return {
            "data": cached["data"],
            "source": "cache",
            "cached": True,
            "stale": False,
            "age_seconds": cached["age_seconds"]
        }

    try:
        response = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{SKEW_SYMBOL}",
            params={
                "interval": "1d",
                "range": "5d",
                "includePrePost": "false",
                "events": "div,splits"
            },
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15
        )
        response.raise_for_status()
        payload = response.json()
        result = ((payload.get("chart") or {}).get("result") or [None])[0]
        if not result:
            raise ValueError("Missing SKEW chart payload")

        closes = (((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
        valid_closes = [float(value) for value in closes if value is not None]
        if not valid_closes:
            raise ValueError("Missing SKEW close values")

        value = round(valid_closes[-1], 2)
        previous = round(valid_closes[-2], 2) if len(valid_closes) > 1 else value
        change = round(value - previous, 2)
        indicator = {
            "name": "CBOE SKEW Index",
            "symbol": SKEW_SYMBOL,
            "value": value,
            "change": change,
            "signal": get_skew_signal(value)
        }
        set_cache_entry(quote_cache, SKEW_CACHE_KEY, indicator)
        return {
            "data": indicator,
            "source": "live",
            "cached": False,
            "stale": False,
            "age_seconds": 0
        }
    except Exception as exc:
        print("SKEW fetch error:", exc)
        if cached:
            return {
                "data": cached["data"],
                "source": "cache",
                "cached": True,
                "stale": True,
                "age_seconds": cached["age_seconds"]
            }
        return None


# =========================
# STRATEGY ENGINE
# =========================

def analyze_strategy(symbol, strategy):
    market = get_data(symbol)
    if not market:
        return None
    skew = fetch_skew_indicator()

    d = market["data"]
    price = d["price"]
    open_price = d["open"]

    change = round(((price - open_price) / open_price) * 100, 2)
    bias = "Bullish" if change > 0 else "Bearish" if change < 0 else "Neutral"
    support = round(price * 0.99, 2)
    resistance = round(price * 1.01, 2)
    skew_value = skew["data"]["value"] if skew else None
    trade_signal = build_trade_signal(change, bias, strategy, skew_value)

    if strategy == "scalp":
        entry = round(price * 1.001, 2)
        stop = round(price * 0.998, 2)
        targets = [round(price * 1.003, 2), round(price * 1.005, 2)]
        summary = "Quick micro-move scalp trade."
    elif strategy == "day":
        entry = round(price * 1.002, 2)
        stop = support
        targets = [resistance, round(resistance * 1.02, 2)]
        summary = "Intraday trend structure trade."
    elif strategy == "swing":
        entry = round(price * 1.01, 2)
        stop = round(price * 0.95, 2)
        targets = [round(price * 1.08, 2), round(price * 1.15, 2)]
        summary = "Multi-day swing position."
    elif strategy == "momentum":
        entry = round(price * 1.005, 2)
        stop = round(price * 0.99, 2)
        targets = [round(price * 1.04, 2), round(price * 1.08, 2)]
        summary = "Momentum breakout continuation."
    elif strategy == "mean":
        entry = round(price * 0.995, 2)
        stop = round(price * 1.01, 2)
        targets = [round(price * 0.98, 2), round(price * 0.96, 2)]
        summary = "Mean reversion setup."
    else:
        entry = price
        stop = support
        targets = [resistance]
        summary = "Default strategy."

    return {
        "ticker": symbol,
        "price": price,
        "change": change,
        "bias": bias,
        "data_source": market["source"],
        "is_demo": market["source"] == "demo",
        "is_cached": market["cached"],
        "is_stale": market["stale"],
        "cache_age_seconds": market["age_seconds"],
        "levels": {
            "support": support,
            "resistance": resistance
        },
        "plan": {
            "entry": entry,
            "stop": stop,
            "targets": targets
        },
        "summary": summary,
        "indicators": {
            "trade_signal": trade_signal,
            "skew": {
                **skew["data"],
                "data_source": skew["source"],
                "is_cached": skew["cached"],
                "is_stale": skew["stale"],
                "cache_age_seconds": skew["age_seconds"]
            } if skew else None
        }
    }


def get_watchlist_snapshot(symbol):
    market = get_data(symbol)
    if not market:
        return None
    candle_result = fetch_and_cache_candles(symbol, "5m")

    quote = market["data"]
    open_price = quote["open"]
    price = quote["price"]
    change = round(((price - open_price) / open_price) * 100, 2) if open_price else 0
    sparkline = []
    if candle_result and candle_result["candles"]:
        sparkline = [round(c["close"], 2) for c in candle_result["candles"][-24:]]

    return {
        "ticker": symbol,
        "price": price,
        "change": change,
        "sparkline": sparkline,
        "data_source": market["source"],
        "is_demo": market["source"] == "demo",
        "is_cached": market["cached"],
        "is_stale": market["stale"]
    }


# =========================
# ROUTES
# =========================

@app.route("/")
def home():
    return send_from_directory("static", "index.html")


@app.route("/analyze")
def analyze():
    symbol = request.args.get("ticker")
    strategy = request.args.get("strategy", "day")

    if not symbol:
        return jsonify({"error": "Missing ticker"}), 400

    result = analyze_strategy(symbol.upper(), strategy)
    if not result:
        return jsonify({"error": "No data"}), 500

    return jsonify(result)


@app.route("/candles")
def candles():
    symbol = request.args.get("ticker")
    tf = request.args.get("tf", "5m")

    if not symbol:
        return jsonify({
            "candles": [],
            "data_source": "none",
            "is_demo": False,
            "is_cached": False,
            "is_stale": False,
            "cache_age_seconds": 0,
            "warning": "Missing ticker"
        })

    result = fetch_and_cache_candles(symbol.upper(), tf)
    if result and result["candles"]:
        display_candles = prepare_chart_candles(result["candles"], tf)
        return jsonify({
            "candles": display_candles,
            "data_source": result["source"],
            "is_demo": False,
            "is_cached": result["cached"],
            "is_stale": result["stale"],
            "cache_age_seconds": result["age_seconds"],
            "warning": "Live market data is temporarily unavailable. Showing cached candles."
            if result["stale"] else None
        })

    demo = get_demo_market(symbol.upper(), tf)["candles"]
    demo_candles = prepare_chart_candles(demo["candles"], tf)
    return jsonify({
        "candles": demo_candles,
        "data_source": "demo",
        "is_demo": True,
        "is_cached": True,
        "is_stale": False,
        "cache_age_seconds": 0,
        "warning": "Live market data is unavailable, so this chart is using demo data."
    })


@app.route("/watchlist", methods=["GET", "POST", "DELETE"])
def watchlist():
    data = load_watchlist()

    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        ticker = (payload.get("ticker") or "").upper().strip()
        if ticker and ticker not in data:
            data.append(ticker)
            save_watchlist(data)

    if request.method == "DELETE":
        payload = request.get_json(silent=True) or {}
        ticker = (payload.get("ticker") or "").upper().strip()
        data = [t for t in data if t != ticker]
        save_watchlist(data)

    return jsonify(data)


@app.route("/watchlist/data")
def watchlist_data():
    raw_tickers = request.args.get("tickers", "").strip()
    if raw_tickers:
        tickers = []
        for ticker in raw_tickers.split(","):
            clean = ticker.upper().strip()
            if clean and clean not in tickers:
                tickers.append(clean)
    else:
        tickers = load_watchlist()
    snapshots = []

    for ticker in tickers:
        snapshot = get_watchlist_snapshot(ticker)
        if snapshot:
            snapshots.append(snapshot)

    return jsonify(snapshots)


# =========================
# RUN
# =========================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=True)
