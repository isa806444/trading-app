from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask, jsonify, request, send_from_directory, session, has_request_context
from flask_cors import CORS
import hashlib
import json
import math
import os
import psycopg2
import requests
import time
from urllib.parse import quote_plus
from werkzeug.security import check_password_hash, generate_password_hash
from xml.etree import ElementTree

app = Flask(__name__, static_folder="static")
CORS(app)

WATCHLIST_FILE = "watchlist.json"
MARKET_CACHE_FILE = "market_cache.json"
TWELVE_DATA_BASE_URL = "https://api.twelvedata.com"
TWELVE_DATA_API_KEY_ENV = "TWELVE_DATA_API_KEY"
DATABASE_URL_ENV = "DATABASE_URL"
QUOTE_CACHE_TTL = 60
LIVE_PRICE_CACHE_TTL = 1
CANDLE_CACHE_TTL = 120
INDICATOR_CACHE_TTL = 900
NEWS_CACHE_TTL = 900
EVENTS_CACHE_TTL = 1800
DEMO_TIMEZONE = ZoneInfo("America/New_York")
STATIC_US_MACRO_EVENTS = [
    ("2026-04-03T08:30:00-04:00", "Employment Situation", "March 2026"),
    ("2026-04-10T08:30:00-04:00", "Consumer Price Index", "March 2026"),
    ("2026-04-14T08:30:00-04:00", "Producer Price Index", "March 2026"),
    ("2026-05-08T08:30:00-04:00", "Employment Situation", "April 2026"),
    ("2026-05-12T08:30:00-04:00", "Consumer Price Index", "April 2026"),
    ("2026-05-13T08:30:00-04:00", "Producer Price Index", "April 2026"),
    ("2026-06-05T08:30:00-04:00", "Employment Situation", "May 2026"),
    ("2026-06-10T08:30:00-04:00", "Consumer Price Index", "May 2026"),
    ("2026-06-11T08:30:00-04:00", "Producer Price Index", "May 2026"),
    ("2026-07-02T08:30:00-04:00", "Employment Situation", "June 2026"),
    ("2026-07-14T08:30:00-04:00", "Consumer Price Index", "June 2026"),
    ("2026-07-15T08:30:00-04:00", "Producer Price Index", "June 2026"),
]

quote_cache = {}
candle_cache = {}
database_enabled = False
news_cache = {}
events_cache = {}


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
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "trading-app-dev-secret")
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True


def get_twelve_data_api_key():
    return os.environ.get(TWELVE_DATA_API_KEY_ENV, "").strip()


def get_database_url():
    direct = os.environ.get(DATABASE_URL_ENV, "").strip()
    if direct:
        return direct

    for key, value in os.environ.items():
        normalized = key.lower()
        if ("database" in normalized or "trading_app_db" in normalized) and "://" in str(value):
            return str(value).strip()

    return ""


# =========================
# STORAGE
# =========================

def get_db_connection():
    database_url = get_database_url()
    if not database_url:
        return None
    return psycopg2.connect(database_url)


def initialize_database():
    global database_enabled

    try:
        conn = get_db_connection()
        if not conn:
            database_enabled = False
            return
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app_state (
                        state_key TEXT PRIMARY KEY,
                        state_value JSONB NOT NULL DEFAULT '[]'::jsonb,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        email TEXT NOT NULL UNIQUE,
                        display_name TEXT NOT NULL,
                        password_hash TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_state (
                        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        state_key TEXT NOT NULL,
                        state_value JSONB NOT NULL DEFAULT '[]'::jsonb,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        PRIMARY KEY (user_id, state_key)
                    )
                    """
                )
        conn.close()
        database_enabled = True
    except Exception as exc:
        print("Database initialization skipped:", exc)
        database_enabled = False


def get_current_user_id():
    if not has_request_context():
        return None
    raw_user_id = session.get("user_id")
    if raw_user_id is None:
        return None
    try:
        return int(raw_user_id)
    except (TypeError, ValueError):
        return None


def normalize_email(value):
    return str(value or "").strip().lower()


def load_state_list(state_key, fallback=None, user_id=None):
    current_user_id = user_id if user_id is not None else get_current_user_id()

    if database_enabled and current_user_id:
        try:
            conn = get_db_connection()
            if conn:
                with conn:
                    with conn.cursor() as cursor:
                        cursor.execute(
                            "SELECT state_value FROM user_state WHERE user_id = %s AND state_key = %s",
                            (current_user_id, state_key)
                        )
                        row = cursor.fetchone()
                        if row and isinstance(row[0], list):
                            conn.close()
                            return row[0]
                conn.close()
        except Exception as exc:
            print(f"Database read failed for {state_key}:", exc)

    return fallback() if fallback else []


def save_state_list(state_key, data, fallback=None, user_id=None):
    cleaned = data if isinstance(data, list) else []
    current_user_id = user_id if user_id is not None else get_current_user_id()

    if database_enabled and current_user_id:
        try:
            conn = get_db_connection()
            if conn:
                with conn:
                    with conn.cursor() as cursor:
                        cursor.execute(
                            """
                            INSERT INTO user_state (user_id, state_key, state_value, updated_at)
                            VALUES (%s, %s, %s::jsonb, NOW())
                            ON CONFLICT (user_id, state_key)
                            DO UPDATE SET
                                state_value = EXCLUDED.state_value,
                                updated_at = NOW()
                            """,
                            (current_user_id, state_key, json.dumps(cleaned))
                        )
                conn.close()
                return
        except Exception as exc:
            print(f"Database write failed for {state_key}:", exc)

    if fallback:
        fallback(cleaned)


def load_watchlist_file_only():
    if not os.path.exists(WATCHLIST_FILE):
        return []
    try:
        with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_watchlist_file_only(data):
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_watchlist():
    return load_state_list("watchlist", load_watchlist_file_only)


def save_watchlist(data):
    save_state_list("watchlist", data, save_watchlist_file_only)


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


initialize_database()
initialize_market_cache()


# =========================
# AUTH
# =========================

def get_user_by_email(email):
    normalized = normalize_email(email)
    if not normalized or not database_enabled:
        return None

    try:
        conn = get_db_connection()
        if not conn:
            return None
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT id, email, display_name, password_hash, created_at FROM users WHERE email = %s",
                    (normalized,)
                )
                row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        return {
            "id": row[0],
            "email": row[1],
            "display_name": row[2],
            "password_hash": row[3],
            "created_at": row[4].isoformat() if row[4] else None
        }
    except Exception as exc:
        print("User lookup failed:", exc)
        return None


def get_user_by_id(user_id):
    if not user_id or not database_enabled:
        return None

    try:
        conn = get_db_connection()
        if not conn:
            return None
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT id, email, display_name, created_at FROM users WHERE id = %s",
                    (user_id,)
                )
                row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        return {
            "id": row[0],
            "email": row[1],
            "display_name": row[2],
            "created_at": row[3].isoformat() if row[3] else None
        }
    except Exception as exc:
        print("User load by id failed:", exc)
        return None


def get_current_user():
    return get_user_by_id(get_current_user_id())


def create_user(email, password, display_name):
    normalized = normalize_email(email)
    clean_name = str(display_name or "").strip() or normalized.split("@")[0]
    if not normalized or "@" not in normalized:
        return None, "Enter a valid email address."
    if len(password or "") < 8:
        return None, "Password must be at least 8 characters."
    if not database_enabled:
        return None, "User accounts are unavailable right now."
    if get_user_by_email(normalized):
        return None, "An account with that email already exists."

    try:
        conn = get_db_connection()
        if not conn:
            return None, "User accounts are unavailable right now."
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO users (email, display_name, password_hash)
                    VALUES (%s, %s, %s)
                    RETURNING id, email, display_name, created_at
                    """,
                    (normalized, clean_name, generate_password_hash(password))
                )
                row = cursor.fetchone()
        conn.close()
        return {
            "id": row[0],
            "email": row[1],
            "display_name": row[2],
            "created_at": row[3].isoformat() if row[3] else None
        }, None
    except Exception as exc:
        print("Create user failed:", exc)
        return None, "Could not create the account right now."


def authenticate_user(email, password):
    user = get_user_by_email(email)
    if not user or not check_password_hash(user["password_hash"], password or ""):
        return None
    return {
        "id": user["id"],
        "email": user["email"],
        "display_name": user["display_name"],
        "created_at": user["created_at"]
    }


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


def get_market_session_name(unix_seconds):
    dt = datetime.fromtimestamp(unix_seconds, tz=DEMO_TIMEZONE)
    minutes = (dt.hour * 60) + dt.minute
    if minutes < 570:
        return "Premarket"
    if minutes < 960:
        return "Regular Hours"
    return "After Hours"


def get_current_market_status():
    now = datetime.now(tz=DEMO_TIMEZONE)
    if now.weekday() >= 5:
        return "Closed"

    minutes = (now.hour * 60) + now.minute
    if 240 <= minutes < 570:
        return "Premarket"
    if 570 <= minutes < 960:
        return "Regular Hours"
    if 960 <= minutes < 1200:
        return "After Hours"
    return "Closed"


def build_quote_from_candles(candles):
    return {
        "price": round(candles[-1]["close"], 2),
        "open": round(candles[0]["open"], 2),
        "high": round(max(c["high"] for c in candles), 2),
        "low": round(min(c["low"] for c in candles), 2)
    }


def calculate_ema(values, period):
    if not values:
        return []

    multiplier = 2 / (period + 1)
    ema_values = []
    ema = values[0]

    for index, value in enumerate(values):
        if index == 0:
            ema = value
        else:
            ema = (value - ema) * multiplier + ema
        ema_values.append(round(ema, 4))

    return ema_values


def calculate_vwap(candles):
    cumulative_pv = 0.0
    cumulative_volume = 0.0
    vwap_values = []

    for candle in candles:
        typical_price = (candle["high"] + candle["low"] + candle["close"]) / 3
        volume = candle.get("volume") or 0
        cumulative_pv += typical_price * volume
        cumulative_volume += volume
        if cumulative_volume <= 0:
            vwap_values.append(round(candle["close"], 4))
        else:
            vwap_values.append(round(cumulative_pv / cumulative_volume, 4))

    return vwap_values


def calculate_rsi(values, period=14):
    if not values:
        return []

    rsi_values = [None]
    gains = []
    losses = []
    avg_gain = None
    avg_loss = None

    for index in range(1, len(values)):
        delta = values[index] - values[index - 1]
        gain = max(delta, 0)
        loss = abs(min(delta, 0))
        gains.append(gain)
        losses.append(loss)

        if index < period:
            rsi_values.append(None)
            continue

        if index == period:
            avg_gain = sum(gains[-period:]) / period
            avg_loss = sum(losses[-period:]) / period
        else:
            avg_gain = ((avg_gain * (period - 1)) + gain) / period
            avg_loss = ((avg_loss * (period - 1)) + loss) / period

        if avg_loss == 0:
            rsi = 100
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))

        rsi_values.append(round(rsi, 2))

    while len(rsi_values) < len(values):
        rsi_values.append(None)

    return rsi_values


def build_chart_indicators(candles):
    closes = [c["close"] for c in candles]
    return {
        "ema9": calculate_ema(closes, 9),
        "ema20": calculate_ema(closes, 20),
        "vwap": calculate_vwap(candles),
        "rsi14": calculate_rsi(closes, 14),
    }


def summarize_news_driver(change, headlines):
    joined = " ".join(headlines).lower()

    if any(word in joined for word in ["earnings", "guidance", "revenue", "profit", "forecast"]):
        category = "earnings or guidance headlines"
    elif any(word in joined for word in ["upgrade", "downgrade", "rating", "price target", "analyst"]):
        category = "analyst rating headlines"
    elif any(word in joined for word in ["deal", "acquisition", "merger", "partnership", "contract"]):
        category = "deal or partnership headlines"
    elif any(word in joined for word in ["launch", "product", "ai", "fda", "chip", "drug"]):
        category = "company catalyst headlines"
    elif any(word in joined for word in ["rates", "inflation", "fed", "tariff", "economy", "market"]):
        category = "macro market headlines"
    else:
        category = "recent company headlines"

    if change > 0:
        return f"Possible reason it's up: {category}."
    if change < 0:
        return f"Possible reason it's down: {category}."
    return f"Possible driver today: {category}."


def build_news_impact(headlines, earnings):
    positive_words = {"beats", "beat", "upgrade", "surge", "record", "strong", "growth", "raises", "partnership"}
    negative_words = {"miss", "downgrade", "cuts", "probe", "lawsuit", "recall", "weak", "drop", "warning"}
    urgency_bonus = 0
    next_earnings = parse_event_datetime((earnings or {}).get("next_earnings_date"))
    if next_earnings:
        days_until = (next_earnings.date() - datetime.now(DEMO_TIMEZONE).date()).days
        if 0 <= days_until <= 7:
            urgency_bonus = 1

    score = urgency_bonus
    for headline in headlines[:5]:
        words = set(str(headline).lower().replace("-", " ").split())
        score += len(words & positive_words)
        score -= len(words & negative_words)

    if score >= 3:
        label = "High positive impact"
        reason = "Headline mix is skewing bullish and could shape near-term attention."
    elif score <= -3:
        label = "High negative impact"
        reason = "Headline mix is skewing bearish and may keep pressure on the name."
    elif abs(score) >= 1:
        label = "Moderate impact"
        reason = "News flow is active enough to matter, but it is not one-sided."
    else:
        label = "Low impact"
        reason = "Headline flow looks light or mixed right now."

    if urgency_bonus:
        reason += " Upcoming earnings are close, so reactions can be sharper."

    return {
        "score": score,
        "label": label,
        "reason": reason
    }


def fetch_stock_news(symbol, change):
    cache_key = f"news:{symbol}"
    cached = get_cache_entry(news_cache, cache_key, NEWS_CACHE_TTL)
    if cached and not cached["stale"]:
        return cached["data"]

    query = quote_plus(f"{symbol} stock when:1d")
    url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"

    try:
        response = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        root = ElementTree.fromstring(response.text)
        items = []

        for item in root.findall(".//item")[:5]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            if title and link:
                items.append({
                    "title": title,
                    "link": link,
                    "published_at": pub_date
                })

        headlines = [item["title"] for item in items[:3]]
        payload = {
            "driver": summarize_news_driver(change, headlines) if headlines else "No fresh headlines were found for this ticker just now.",
            "articles": items
        }
        set_cache_entry(news_cache, cache_key, payload)
        return payload
    except Exception as exc:
        print("News fetch error:", exc)
        if cached:
            return cached["data"]
        return {
            "driver": "No fresh headlines were available for this ticker just now.",
            "articles": []
        }


def parse_event_datetime(value):
    raw = str(value or "").strip()
    if not raw:
        return None

    if raw.startswith("/Date(") and raw.endswith(")/"):
        digits = "".join(ch for ch in raw if ch.isdigit())
        if digits:
            return datetime.fromtimestamp(int(digits) / 1000, tz=DEMO_TIMEZONE)

    cleaned = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=DEMO_TIMEZONE)
        return dt.astimezone(DEMO_TIMEZONE)
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.replace(tzinfo=DEMO_TIMEZONE)
        except ValueError:
            continue

    return None


def format_event_dt(dt):
    if not dt:
        return None
    return dt.astimezone(DEMO_TIMEZONE).isoformat()


def fetch_earnings_dates(symbol):
    cache_key = f"earnings:{symbol}"
    cached = get_cache_entry(events_cache, cache_key, EVENTS_CACHE_TTL)
    if cached and not cached["stale"]:
        return cached["data"]

    try:
        api_key = get_twelve_data_api_key()
        if not api_key:
            raise ValueError("Missing Twelve Data API key for earnings")

        response = requests.get(
            f"{TWELVE_DATA_BASE_URL}/earnings",
            params={"symbol": symbol, "apikey": api_key},
            timeout=15
        )
        response.raise_for_status()
        payload = response.json()
        earnings = payload.get("earnings") or []
        normalized_dates = [parse_event_datetime(item.get("date")) for item in earnings if isinstance(item, dict)]
        normalized_dates = [dt for dt in normalized_dates if dt]
        normalized_dates.sort()
        now = datetime.now(tz=DEMO_TIMEZONE)
        next_date = next((dt for dt in normalized_dates if dt >= now), None)
        most_recent = None
        for dt in normalized_dates:
            if dt <= now:
                most_recent = dt

        next_row = next((item for item in earnings if parse_event_datetime(item.get("date")) == next_date), None) if next_date else None
        payload = {
            "next_earnings_date": format_event_dt(next_date),
            "recent_earnings_date": format_event_dt(most_recent),
            "eps_estimate": next_row.get("eps_estimate") if isinstance(next_row, dict) else None,
            "revenue_estimate": None,
            "source": "Twelve Data"
        }
        set_cache_entry(events_cache, cache_key, payload)
        return payload
    except Exception as exc:
        print("Earnings date fetch error:", exc)
        if cached:
            return cached["data"]
        return {
            "next_earnings_date": None,
            "recent_earnings_date": None,
            "eps_estimate": None,
            "revenue_estimate": None,
            "source": "Unavailable"
        }


def fetch_economic_calendar():
    cache_key = "economic_calendar:us"
    cached = get_cache_entry(events_cache, cache_key, EVENTS_CACHE_TTL)
    if cached and not cached["stale"]:
        return cached["data"]

    try:
        now = datetime.now(tz=DEMO_TIMEZONE)
        upcoming = []
        for raw_date, event_name, reference in STATIC_US_MACRO_EVENTS:
            dt = parse_event_datetime(raw_date)
            if not dt or dt < now:
                continue
            upcoming.append({
                "date": format_event_dt(dt),
                "event": event_name,
                "category": "US Macro",
                "importance": "High",
                "reference": reference,
                "actual": None,
                "forecast": None,
                "previous": None,
                "source": "BLS"
            })
        upcoming.sort(key=lambda item: item["date"] or "")
        result = upcoming[:8]
        set_cache_entry(events_cache, cache_key, result)
        return result
    except Exception as exc:
        print("Economic calendar fetch error:", exc)
        if cached:
            return cached["data"]
        return []


def fetch_market_events(symbol):
    return {
        "earnings": fetch_earnings_dates(symbol),
        "economic_calendar": fetch_economic_calendar()
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


def fetch_twelve_data_price(symbol):
    api_key = get_twelve_data_api_key()
    if not api_key:
        return None

    response = requests.get(
        f"{TWELVE_DATA_BASE_URL}/price",
        params={
            "apikey": api_key,
            "symbol": symbol,
            "format": "JSON",
        },
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()

    if payload.get("status") == "error":
        raise ValueError(payload.get("message", "Unknown Twelve Data price error"))

    price = payload.get("price")
    if price is None:
        return None
    return round(float(price), 2)


def search_symbols(query):
    api_key = get_twelve_data_api_key()
    if not api_key or len(query.strip()) < 1:
        return []

    normalized_query = query.strip().upper()

    try:
        response = requests.get(
            f"{TWELVE_DATA_BASE_URL}/symbol_search",
            params={
                "apikey": api_key,
                "symbol": query.strip(),
                "outputsize": 8,
                "show_plan": "false"
            },
            timeout=15
        )
        response.raise_for_status()
        payload = response.json()
        matches = payload.get("data") or []
        results = []

        allowed_exchanges = {"NASDAQ", "NYSE", "AMEX", "ARCA"}

        for item in matches:
            symbol = (item.get("symbol") or "").strip()
            name = (item.get("instrument_name") or item.get("name") or "").strip()
            exchange = (item.get("exchange") or "").strip()
            country = (item.get("country") or "").strip()
            instrument_type = str(item.get("instrument_type") or "").strip().lower()
            normalized_symbol = symbol.upper()
            normalized_name = name.upper()

            if not symbol or not name:
                continue
            if country and country.upper() not in {"USA", "UNITED STATES"}:
                continue
            if exchange and exchange.upper() not in allowed_exchanges:
                continue
            if instrument_type and instrument_type not in {"common_stock", "stock", "dr"}:
                continue
            if not normalized_symbol.replace(".", "").replace("-", "").isalnum():
                continue

            score = 0
            if normalized_symbol == normalized_query:
                score += 120
            elif normalized_symbol.startswith(normalized_query):
                score += 90
            elif normalized_query in normalized_symbol:
                score += 60

            if normalized_name == normalized_query:
                score += 110
            elif normalized_name.startswith(normalized_query):
                score += 85
            elif normalized_query in normalized_name:
                score += 50

            if exchange.upper() == "NASDAQ":
                score += 8
            elif exchange.upper() == "NYSE":
                score += 6

            if len(normalized_symbol) <= 5:
                score += 4

            if score <= 0:
                continue

            results.append({
                "symbol": symbol,
                "name": name,
                "exchange": exchange,
                "country": country,
                "score": score
            })

        results.sort(key=lambda item: (-item["score"], len(item["symbol"]), item["symbol"]))
        deduped = []
        seen_symbols = set()

        for item in results:
            if item["symbol"] in seen_symbols:
                continue
            seen_symbols.add(item["symbol"])
            deduped.append({
                "symbol": item["symbol"],
                "name": item["name"],
                "exchange": item["exchange"],
                "country": item["country"]
            })
            if len(deduped) >= 6:
                break

        return deduped
    except Exception as exc:
        print("Symbol search error:", exc)
        return []


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


def get_live_price(symbol):
    cache_key = f"live:{symbol}"
    cached = get_cache_entry(quote_cache, cache_key, LIVE_PRICE_CACHE_TTL)
    if cached and not cached["stale"]:
        return {
            "price": cached["data"]["price"],
            "source": "cache",
            "is_cached": True,
            "is_stale": False,
            "cache_age_seconds": cached["age_seconds"]
        }

    try:
        live_price = fetch_twelve_data_price(symbol)
        if live_price is not None:
            set_cache_entry(quote_cache, cache_key, {"price": live_price})
            return {
                "price": live_price,
                "source": "live",
                "is_cached": False,
                "is_stale": False,
                "cache_age_seconds": 0
            }
    except Exception as exc:
        print("Live price fetch error:", exc)

    if cached:
        return {
            "price": cached["data"]["price"],
            "source": "cache",
            "is_cached": True,
            "is_stale": True,
            "cache_age_seconds": cached["age_seconds"]
        }

    market = get_data(symbol)
    if market:
        return {
            "price": market["data"]["price"],
            "source": market["source"],
            "is_cached": market["cached"],
            "is_stale": market["stale"],
            "cache_age_seconds": market["age_seconds"]
        }

    return None


def build_trade_signal(change, bias, strategy, candles):
    closes = [c["close"] for c in candles]
    ema9 = calculate_ema(closes, 9)
    ema20 = calculate_ema(closes, 20)
    vwap = calculate_vwap(candles)
    rsi = calculate_rsi(closes, 14)

    last_close = closes[-1] if closes else 0
    prev_close = closes[-2] if len(closes) > 1 else last_close
    last_ema9 = ema9[-1] if ema9 else last_close
    last_ema20 = ema20[-1] if ema20 else last_close
    last_vwap = vwap[-1] if vwap else last_close
    last_rsi = rsi[-1] if rsi and rsi[-1] is not None else 50
    higher_low = candles[-1]["low"] >= candles[-2]["low"] if len(candles) > 1 else False
    lower_high = candles[-1]["high"] <= candles[-2]["high"] if len(candles) > 1 else False
    bullish_stack = last_close > last_ema9 > last_ema20 and last_close > last_vwap
    bearish_stack = last_close < last_ema9 < last_ema20 and last_close < last_vwap
    bullish_confirmations = []
    bearish_confirmations = []

    if bullish_stack:
        bullish_confirmations.append("price is stacked above EMA 9, EMA 20, and VWAP")
    if bearish_stack:
        bearish_confirmations.append("price is stacked below EMA 9, EMA 20, and VWAP")

    if last_rsi >= 55:
        bullish_confirmations.append(f"RSI is showing strength at {round(last_rsi, 1)}")
    elif last_rsi <= 45:
        bearish_confirmations.append(f"RSI is showing weakness at {round(last_rsi, 1)}")

    if higher_low:
        bullish_confirmations.append("the latest candle held a higher low")
    if lower_high:
        bearish_confirmations.append("the latest candle printed a lower high")

    if change >= 0.5:
        bullish_confirmations.append(f"the stock is up {change}% on the day")
    elif change <= -0.5:
        bearish_confirmations.append(f"the stock is down {abs(change)}% on the day")

    if bias == "Bullish":
        bullish_confirmations.append("overall session bias is bullish")
    elif bias == "Bearish":
        bearish_confirmations.append("overall session bias is bearish")

    long_score = len(bullish_confirmations) + (1 if last_close > prev_close else 0)
    short_score = len(bearish_confirmations) + (1 if last_close < prev_close else 0)
    dominant_score = max(long_score, short_score)
    opposing_score = min(long_score, short_score)

    if long_score >= 4 and long_score >= short_score + 2:
        action = "BUY"
        tone = "bullish"
        grade = "A"
        strength = "Strong"
        confirmations = bullish_confirmations
        reason = f"This {strategy} setup looks strong because " + ", ".join(confirmations[:3]) + "."
    elif short_score >= 4 and short_score >= long_score + 2:
        action = "SELL"
        tone = "bearish"
        grade = "A"
        strength = "Strong"
        confirmations = bearish_confirmations
        reason = f"This {strategy} setup looks strong because " + ", ".join(confirmations[:3]) + "."
    elif long_score >= 3 and long_score > short_score:
        action = "BUY"
        tone = "bullish"
        grade = "B"
        strength = "Moderate"
        confirmations = bullish_confirmations
        reason = f"This {strategy} setup has enough bullish confirmation to be usable, mainly because " + ", ".join(confirmations[:3]) + "."
    elif short_score >= 3 and short_score > long_score:
        action = "SELL"
        tone = "bearish"
        grade = "B"
        strength = "Moderate"
        confirmations = bearish_confirmations
        reason = f"This {strategy} setup has enough bearish confirmation to be usable, mainly because " + ", ".join(confirmations[:3]) + "."
    elif dominant_score >= 2 and dominant_score > opposing_score:
        action = "WAIT"
        tone = "neutral"
        grade = "C"
        strength = "Mixed"
        confirmations = bullish_confirmations if long_score > short_score else bearish_confirmations
        reason = f"There is some directional evidence here, but it is not strong enough yet. Right now the clearest signs are that " + ", ".join(confirmations[:2]) + "."
    else:
        action = "WAIT"
        tone = "neutral"
        grade = "C"
        strength = "Weak"
        confirmations = []
        reason = "This setup is still too mixed. Price, trend structure, and momentum are not lined up well enough to call it strong."

    return {
        "action": action,
        "tone": tone,
        "grade": grade,
        "strength": strength,
        "confirmations": confirmations[:4],
        "reason": reason,
        "score": dominant_score,
        "confidence": min(95, 45 + (dominant_score * 10) - (opposing_score * 5))
    }


# =========================
# STRATEGY ENGINE
# =========================

def analyze_strategy(symbol, strategy):
    market = get_data(symbol)
    if not market:
        return None
    candle_result = fetch_and_cache_candles(symbol, "5m")

    d = market["data"]
    price = d["price"]
    open_price = d["open"]
    signal_candles = candle_result["candles"] if candle_result and candle_result["candles"] else build_demo_candles(symbol, "5m")

    dollar_change = round(price - open_price, 2)
    change = round(((price - open_price) / open_price) * 100, 2)
    bias = "Bullish" if change > 0 else "Bearish" if change < 0 else "Neutral"
    support = round(price * 0.99, 2)
    resistance = round(price * 1.01, 2)
    trade_signal = build_trade_signal(change, bias, strategy, signal_candles)
    news = fetch_stock_news(symbol, change)
    events = fetch_market_events(symbol)
    news["impact"] = build_news_impact(
        [article.get("title", "") for article in news.get("articles", [])],
        events.get("earnings") if isinstance(events, dict) else {}
    )

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
        "open_price": open_price,
        "dollar_change": dollar_change,
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
        "news": news,
        "events": events,
        "indicators": {
            "trade_signal": trade_signal
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


def load_paper_positions():
    return load_state_list("paper_positions")


def save_paper_positions(data):
    save_state_list("paper_positions", data)


def load_paper_history():
    return load_state_list("paper_history")


def save_paper_history(data):
    save_state_list("paper_history", data)


def load_alerts():
    return load_state_list("alerts")


def save_alerts(data):
    save_state_list("alerts", data)


# =========================
# ROUTES
# =========================

@app.route("/")
def home():
    return send_from_directory("static", "index.html")


@app.route("/auth/status")
def auth_status():
    user = get_current_user()
    return jsonify({
        "database_enabled": database_enabled,
        "authenticated": bool(user),
        "user": user
    })


@app.route("/auth/signup", methods=["POST"])
def auth_signup():
    payload = request.get_json(silent=True) or {}
    user, error = create_user(
        payload.get("email"),
        payload.get("password"),
        payload.get("display_name")
    )
    if error:
        return jsonify({"error": error}), 400

    session["user_id"] = user["id"]
    return jsonify({
        "ok": True,
        "authenticated": True,
        "user": user
    })


@app.route("/auth/login", methods=["POST"])
def auth_login():
    payload = request.get_json(silent=True) or {}
    user = authenticate_user(payload.get("email"), payload.get("password"))
    if not user:
        return jsonify({"error": "Email or password was incorrect."}), 401

    session["user_id"] = user["id"]
    return jsonify({
        "ok": True,
        "authenticated": True,
        "user": user
    })


@app.route("/auth/logout", methods=["POST"])
def auth_logout():
    session.pop("user_id", None)
    return jsonify({"ok": True, "authenticated": False})


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
        latest_candle_session = get_market_session_name(display_candles[-1]["time"]) if display_candles else "Regular Hours"
        return jsonify({
            "candles": display_candles,
            "indicators": build_chart_indicators(display_candles),
            "market_session": get_current_market_status(),
            "latest_candle_session": latest_candle_session,
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
        "indicators": build_chart_indicators(demo_candles),
        "market_session": get_current_market_status(),
        "latest_candle_session": get_market_session_name(demo_candles[-1]["time"]) if demo_candles else "Regular Hours",
        "data_source": "demo",
        "is_demo": True,
        "is_cached": True,
        "is_stale": False,
        "cache_age_seconds": 0,
        "warning": "Live market data is unavailable, so this chart is using demo data."
    })


@app.route("/live-price")
def live_price():
    symbol = request.args.get("ticker")
    if not symbol:
        return jsonify({"error": "Missing ticker"}), 400

    result = get_live_price(symbol.upper())
    if not result:
        return jsonify({"error": "No live price"}), 500

    return jsonify({
        "ticker": symbol.upper(),
        "price": result["price"],
        "data_source": result["source"],
        "is_cached": result["is_cached"],
        "is_stale": result["is_stale"],
        "cache_age_seconds": result["cache_age_seconds"],
        "timestamp": int(time.time())
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


@app.route("/search-symbols")
def search_symbols_route():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify([])

    return jsonify(search_symbols(query))


@app.route("/app-state", methods=["GET", "POST"])
def app_state():
    current_user = get_current_user()

    if request.method == "GET":
        return jsonify({
            "database_enabled": database_enabled,
            "authenticated": bool(current_user),
            "user": current_user,
            "watchlist": load_watchlist(),
            "paper_positions": load_paper_positions(),
            "paper_history": load_paper_history(),
            "alerts": load_alerts()
        })

    payload = request.get_json(silent=True) or {}

    if "watchlist" in payload:
        cleaned_watchlist = []
        for ticker in payload.get("watchlist") or []:
            normalized = str(ticker).upper().strip()
            if normalized and normalized not in cleaned_watchlist:
                cleaned_watchlist.append(normalized)
        save_watchlist(cleaned_watchlist)

    if "paper_positions" in payload:
        save_paper_positions(payload.get("paper_positions") or [])

    if "paper_history" in payload:
        save_paper_history(payload.get("paper_history") or [])

    if "alerts" in payload:
        save_alerts(payload.get("alerts") or [])

    return jsonify({
        "ok": True,
        "database_enabled": database_enabled,
        "authenticated": bool(current_user),
        "user": current_user
    })


# =========================
# RUN
# =========================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=True)
