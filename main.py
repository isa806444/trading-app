from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask, Response, jsonify, request, send_from_directory, session, has_request_context
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
POLYGON_BASE_URL = "https://api.polygon.io"
POLYGON_API_KEY_ENV = "POLYGON_API_KEY"
TRADOVATE_ENV_ENV = "TRADOVATE_ENV"
TRADOVATE_USERNAME_ENV = "TRADOVATE_USERNAME"
TRADOVATE_PASSWORD_ENV = "TRADOVATE_PASSWORD"
TRADOVATE_APP_ID_ENV = "TRADOVATE_APP_ID"
TRADOVATE_APP_VERSION_ENV = "TRADOVATE_APP_VERSION"
TRADOVATE_CID_ENV = "TRADOVATE_CID"
TRADOVATE_SECRET_ENV = "TRADOVATE_SECRET"
TRADOVATE_DEVICE_ID_ENV = "TRADOVATE_DEVICE_ID"
TRADOVATE_ACCOUNT_SPEC_ENV = "TRADOVATE_ACCOUNT_SPEC"
TRADOVATE_ACCOUNT_ID_ENV = "TRADOVATE_ACCOUNT_ID"
TRADOVATE_REST_URL_ENV = "TRADOVATE_REST_URL"
TRADOVATE_MD_WS_URL_ENV = "TRADOVATE_MD_WS_URL"
TRADOVATE_SYMBOL_MAP_ENV = "TRADOVATE_SYMBOL_MAP"
TRADOVATE_AUTO_TRADE_ENABLED_ENV = "TRADOVATE_AUTO_TRADE_ENABLED"
TRADOVATE_LIVE_TRADING_ACK_ENV = "TRADOVATE_LIVE_TRADING_ACK"
TRADOVATE_DEFAULT_ORDER_QTY_ENV = "TRADOVATE_DEFAULT_ORDER_QTY"
TRADOVATE_MAX_ORDER_QTY_ENV = "TRADOVATE_MAX_ORDER_QTY"
TRADOVATE_MAX_DAILY_ORDERS_ENV = "TRADOVATE_MAX_DAILY_ORDERS"
ALGO_MIN_EDGE_FOR_AUTO_TRADE_ENV = "ALGO_MIN_EDGE_FOR_AUTO_TRADE"
ALGO_DEFAULT_TARGET_PCT_ENV = "ALGO_DEFAULT_TARGET_PCT"
ALGO_DEFAULT_STOP_PCT_ENV = "ALGO_DEFAULT_STOP_PCT"
TRADINGVIEW_WEBHOOK_SECRET_ENV = "TRADINGVIEW_WEBHOOK_SECRET"
DATABASE_URL_ENV = "DATABASE_URL"
QUOTE_CACHE_TTL = 90
LIVE_PRICE_CACHE_TTL = 5
CANDLE_CACHE_TTL = 180
PREVIOUS_CLOSE_CACHE_TTL = 60 * 60
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
ALGORITHM_DEFAULT_UNIVERSE = ["AAPL", "NVDA", "TSLA", "AMD", "META", "AMZN", "MSFT", "GOOGL", "PLTR", "SPY"]
ALGORITHM_SIGNAL_CAPITAL = 1000
TRADOVATE_INDEX_ROOTS = {"ES", "MES", "NQ", "MNQ", "YM", "MYM", "RTY", "M2K"}

quote_cache = {}
candle_cache = {}
tradovate_token_cache = {}
tradingview_alert_messages = []
tradingview_recent_signal_keys = {}
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
def get_polygon_api_key():
    return os.environ.get(POLYGON_API_KEY_ENV, "").strip()


def get_tradovate_env():
    env = os.environ.get(TRADOVATE_ENV_ENV, "demo").strip().lower()
    return "live" if env == "live" else "demo"


def get_tradovate_rest_url():
    configured = os.environ.get(TRADOVATE_REST_URL_ENV, "").strip()
    if configured:
        return configured.rstrip("/")
    return "https://live.tradovateapi.com/v1" if get_tradovate_env() == "live" else "https://demo.tradovateapi.com/v1"


def get_tradovate_md_ws_url():
    configured = os.environ.get(TRADOVATE_MD_WS_URL_ENV, "").strip()
    if configured:
        return configured
    return "wss://md.tradovateapi.com/v1/websocket"


def tradovate_configured():
    required = [
        TRADOVATE_USERNAME_ENV,
        TRADOVATE_PASSWORD_ENV,
        TRADOVATE_APP_ID_ENV,
        TRADOVATE_CID_ENV,
        TRADOVATE_SECRET_ENV
    ]
    return all(os.environ.get(key, "").strip() for key in required)


def tradingview_webhook_secret():
    return os.environ.get(TRADINGVIEW_WEBHOOK_SECRET_ENV, "").strip()


def env_bool(key, default=False):
    raw = os.environ.get(key, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


def env_float(key, default):
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def env_int(key, default):
    try:
        return int(float(os.environ.get(key, default)))
    except (TypeError, ValueError):
        return int(default)


def tradovate_auto_trade_enabled():
    return env_bool(TRADOVATE_AUTO_TRADE_ENABLED_ENV, False)


def live_trading_acknowledged():
    if get_tradovate_env() != "live":
        return True
    return os.environ.get(TRADOVATE_LIVE_TRADING_ACK_ENV, "").strip() == "I_UNDERSTAND_REAL_MONEY_RISK"


def tradovate_execution_ready():
    return tradovate_configured() and tradovate_auto_trade_enabled() and live_trading_acknowledged()


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
                cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS public_profile BOOLEAN NOT NULL DEFAULT FALSE")
                cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS public_alias TEXT")
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
                    """
                    SELECT id, email, display_name, password_hash, created_at,
                           public_profile, public_alias
                    FROM users
                    WHERE email = %s
                    """,
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
            "created_at": row[4].isoformat() if row[4] else None,
            "public_profile": bool(row[5]) if row[5] is not None else False,
            "public_alias": row[6] or row[2]
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
                    """
                    SELECT id, email, display_name, created_at,
                           public_profile, public_alias
                    FROM users
                    WHERE id = %s
                    """,
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
            "created_at": row[3].isoformat() if row[3] else None,
            "public_profile": bool(row[4]) if row[4] is not None else False,
            "public_alias": row[5] or row[2]
        }
    except Exception as exc:
        print("User load by id failed:", exc)
        return None


def get_current_user():
    return get_user_by_id(get_current_user_id())


def serialize_user(user):
    if not user:
        return None
    return {
        "id": user["id"],
        "email": user["email"],
        "display_name": user["display_name"],
        "created_at": user["created_at"],
        "public_profile": bool(user.get("public_profile")),
        "public_alias": user.get("public_alias") or user["display_name"]
    }


def update_user_profile_fields(user_id, public_profile=None, public_alias=None):
    if not database_enabled or not user_id:
        return
    try:
        conn = get_db_connection()
        if not conn:
            return
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE users
                    SET public_profile = COALESCE(%s, public_profile),
                        public_alias = COALESCE(%s, public_alias)
                    WHERE id = %s
                    """,
                    (public_profile, public_alias, user_id)
                )
        conn.close()
    except Exception as exc:
        print("Profile update failed:", exc)


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
            "created_at": row[3].isoformat() if row[3] else None,
            "public_profile": False,
            "public_alias": row[2]
        }, None
    except Exception as exc:
        print("Create user failed:", exc)
        return None, "Could not create the account right now."


def authenticate_user(email, password):
    user = get_user_by_email(email)
    if not user or not check_password_hash(user["password_hash"], password or ""):
        return None
    return serialize_user(user)


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


def parse_polygon_timestamp(raw_value):
    try:
        return int(float(raw_value) / 1000)
    except (TypeError, ValueError):
        return int(time.time())


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
    if not candles:
        return {"price": 0, "open": 0, "high": 0, "low": 0}
    return {
        "price": round(candles[-1]["close"], 2),
        "open": round(candles[0]["open"], 2),
        "high": round(max(c["high"] for c in candles), 2),
        "low": round(min(c["low"] for c in candles), 2)
    }


def build_latest_session_quote(candles):
    if not candles:
        return build_quote_from_candles(candles)

    latest_day = get_et_session_key(candles[-1]["time"])
    latest_day_candles = [c for c in candles if get_et_session_key(c["time"]) == latest_day]
    if not latest_day_candles:
        latest_day_candles = candles

    if get_current_market_status() == "Closed":
        regular_hours_candles = [c for c in latest_day_candles if get_market_session_name(c["time"]) == "Regular Hours"]
        if regular_hours_candles:
            return build_quote_from_candles(regular_hours_candles)

    return build_quote_from_candles(latest_day_candles)


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
        "liquidity_map": build_liquidity_map(candles)
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
        "cached": preferred_source == "cache",
        "stale": cached["stale"],
        "age_seconds": cached["age_seconds"]
    }


def get_polygon_range_config(tf):
    if tf == "1m":
        return {"multiplier": 1, "timespan": "minute", "days": 1}
    if tf == "5m":
        return {"multiplier": 5, "timespan": "minute", "days": 5}
    if tf == "15m":
        return {"multiplier": 15, "timespan": "minute", "days": 10}
    if tf == "1d":
        return {"multiplier": 1, "timespan": "day", "days": 60}
    return {"multiplier": 5, "timespan": "minute", "days": 5}


def parse_market_timestamp(value):
    if value is None:
        return None
    try:
        if hasattr(value, "timestamp"):
            return int(value.timestamp())
        if isinstance(value, (int, float)):
            return int(value / 1_000_000_000) if value > 10_000_000_000 else int(value)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return int(parsed.timestamp())
    except (TypeError, ValueError, OSError):
        return None


def parse_tradovate_expiration_time(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return time.time() + (80 * 60)


def get_tradovate_access_token(token_type="trade"):
    cache_key = "md_token" if token_type == "market" else "token"
    cached = tradovate_token_cache.get(cache_key)
    if cached and time.time() < tradovate_token_cache.get("expires_at", 0) - 300:
        return cached

    if not tradovate_configured():
        return None

    payload = {
        "name": os.environ.get(TRADOVATE_USERNAME_ENV, "").strip(),
        "password": os.environ.get(TRADOVATE_PASSWORD_ENV, "").strip(),
        "appId": os.environ.get(TRADOVATE_APP_ID_ENV, "").strip(),
        "appVersion": os.environ.get(TRADOVATE_APP_VERSION_ENV, "1.0.0").strip() or "1.0.0",
        "cid": os.environ.get(TRADOVATE_CID_ENV, "").strip(),
        "sec": os.environ.get(TRADOVATE_SECRET_ENV, "").strip()
    }
    device_id = os.environ.get(TRADOVATE_DEVICE_ID_ENV, "").strip()
    if device_id:
        payload["deviceId"] = device_id

    response = requests.post(
        f"{get_tradovate_rest_url()}/auth/accesstokenrequest",
        json=payload,
        timeout=12,
    )
    response.raise_for_status()
    data = response.json()
    trade_token = data.get("accessToken")
    market_token = data.get("mdAccessToken") or trade_token
    if not trade_token and not market_token:
        raise RuntimeError(data.get("errorText") or "Tradovate did not return an access token.")

    tradovate_token_cache["token"] = trade_token
    tradovate_token_cache["md_token"] = market_token
    tradovate_token_cache["expires_at"] = parse_tradovate_expiration_time(data.get("expirationTime"))
    return market_token if token_type == "market" else trade_token


def parse_tradovate_symbol_map():
    raw = os.environ.get(TRADOVATE_SYMBOL_MAP_ENV, "").strip()
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
        if isinstance(loaded, dict):
            return {str(k).upper(): str(v).upper() for k, v in loaded.items()}
    except json.JSONDecodeError:
        pass

    mapping = {}
    for pair in raw.split(","):
        if "=" not in pair:
            continue
        key, value = pair.split("=", 1)
        mapping[key.strip().upper()] = value.strip().upper()
    return mapping


def get_front_month_code(now=None):
    now = now or datetime.now(DEMO_TIMEZONE)
    quarter_months = [(3, "H"), (6, "M"), (9, "U"), (12, "Z")]
    for month, code in quarter_months:
        if now.month <= month:
            return f"{code}{str(now.year)[-1]}"
    return f"H{str(now.year + 1)[-1]}"


def normalize_tradovate_symbol(symbol):
    clean = str(symbol or "").upper().strip()
    if not clean:
        return clean
    mapping = parse_tradovate_symbol_map()
    if clean in mapping:
        return mapping[clean]
    if clean in TRADOVATE_INDEX_ROOTS:
        return f"{clean}{get_front_month_code()}"
    return clean


def get_tradovate_chart_config(tf):
    if tf == "1d":
        return {"underlyingType": "DailyBar", "elementSize": 1, "asMuchAsElements": 120}
    if tf == "15m":
        return {"underlyingType": "MinuteBar", "elementSize": 15, "asMuchAsElements": 160}
    if tf == "1m":
        return {"underlyingType": "MinuteBar", "elementSize": 1, "asMuchAsElements": 240}
    return {"underlyingType": "MinuteBar", "elementSize": 5, "asMuchAsElements": 180}


def decode_tradovate_socket_payload(raw_message):
    if not raw_message or raw_message in {"o", "h"}:
        return []
    try:
        if raw_message.startswith("a"):
            return json.loads(raw_message[1:])
        parsed = json.loads(raw_message)
        return parsed if isinstance(parsed, list) else [parsed]
    except (json.JSONDecodeError, TypeError):
        return []


def normalize_tradovate_chart_bars(charts):
    candles = []
    for chart in charts or []:
        for bar in chart.get("bars") or []:
            timestamp = parse_market_timestamp(bar.get("timestamp"))
            if timestamp is None:
                continue
            try:
                open_price = float(bar.get("open"))
                high_price = float(bar.get("high"))
                low_price = float(bar.get("low"))
                close_price = float(bar.get("close"))
                volume = int(float(bar.get("upVolume") or 0) + float(bar.get("downVolume") or 0))
            except (TypeError, ValueError):
                continue
            candles.append({
                "time": timestamp,
                "open": round(open_price, 4),
                "high": round(max(high_price, open_price, close_price), 4),
                "low": round(min(low_price, open_price, close_price), 4),
                "close": round(close_price, 4),
                "volume": volume
            })
    candles.sort(key=lambda item: item["time"])
    return candles


def fetch_tradovate_candles(symbol, tf):
    if not tradovate_configured():
        return None

    try:
        import websocket
    except ImportError:
        print("websocket-client is not installed. Install requirements to enable Tradovate live data.")
        return None

    token = get_tradovate_access_token("market")
    if not token:
        return None

    config = get_tradovate_chart_config(tf)
    request_body = {
        "symbol": normalize_tradovate_symbol(symbol),
        "chartDescription": {
            "underlyingType": config["underlyingType"],
            "elementSize": config["elementSize"],
            "elementSizeUnit": "UnderlyingUnits",
            "withHistogram": False
        },
        "timeRange": {
            "asMuchAsElements": config["asMuchAsElements"]
        }
    }
    candles = []
    realtime_id = None
    ws = None

    try:
        ws = websocket.create_connection(get_tradovate_md_ws_url(), timeout=12)
        raw = ws.recv()
        if raw == "o":
            ws.send(f"authorize\n0\n\n{token}")
        ws.send(f"md/getChart\n1\n\n{json.dumps(request_body)}")

        started = time.time()
        while time.time() - started < 12:
            for item in decode_tradovate_socket_payload(ws.recv()):
                if item.get("s") and item.get("s") >= 400:
                    print("Tradovate chart error:", item.get("d"))
                    return None
                if item.get("s") == 200 and isinstance(item.get("d"), dict):
                    realtime_id = item["d"].get("realtimeId")
                if item.get("e") == "chart":
                    charts = (item.get("d") or {}).get("charts") or []
                    candles.extend(normalize_tradovate_chart_bars(charts))
                    if candles and any(chart.get("eoh") for chart in charts):
                        unique = {candle["time"]: candle for candle in candles}
                        return [unique[key] for key in sorted(unique)]
    except Exception as exc:
        print("Tradovate candle fetch error:", exc)
    finally:
        try:
            if ws and realtime_id:
                ws.send(f"md/cancelChart\n2\n\n{json.dumps({'subscriptionId': realtime_id})}")
            if ws:
                ws.close()
        except Exception:
            pass

    if candles:
        unique = {candle["time"]: candle for candle in candles}
        return [unique[key] for key in sorted(unique)]
    return None


def get_tradovate_account_spec():
    return (
        os.environ.get(TRADOVATE_ACCOUNT_SPEC_ENV, "").strip()
        or os.environ.get(TRADOVATE_USERNAME_ENV, "").strip()
    )


def get_tradovate_account_id():
    raw = os.environ.get(TRADOVATE_ACCOUNT_ID_ENV, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def tradovate_headers():
    token = get_tradovate_access_token("trade")
    if not token:
        raise RuntimeError("Tradovate trade token is unavailable.")
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }


def fetch_tradovate_accounts():
    response = requests.get(
        f"{get_tradovate_rest_url()}/account/list",
        headers=tradovate_headers(),
        timeout=12
    )
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, list) else []


def resolve_tradovate_account():
    configured_id = get_tradovate_account_id()
    configured_spec = get_tradovate_account_spec()
    if configured_id:
        return {"accountId": configured_id, "accountSpec": configured_spec}

    if get_tradovate_env() == "live":
        raise RuntimeError("Set TRADOVATE_ACCOUNT_ID before enabling live Tradovate orders.")

    accounts = fetch_tradovate_accounts()
    if configured_spec:
        for account in accounts:
            if str(account.get("name", "")).upper() == configured_spec.upper():
                return {"accountId": account.get("id"), "accountSpec": account.get("name") or configured_spec}

    active_accounts = [account for account in accounts if account.get("active", True)]
    if active_accounts:
        account = active_accounts[0]
        return {"accountId": account.get("id"), "accountSpec": account.get("name") or configured_spec}

    raise RuntimeError("No Tradovate account was found for order routing.")


def normalize_tradingview_action(value):
    clean = str(value or "").upper().strip().replace("-", "_").replace(" ", "_")
    aliases = {
        "LONG": "BUY",
        "ENTER_LONG": "BUY",
        "BUY_TO_OPEN": "BUY",
        "SHORT": "SELL",
        "ENTER_SHORT": "SELL",
        "SELL_SHORT": "SELL",
        "SELL_TO_OPEN": "SELL",
        "CLOSE_LONG": "EXIT_LONG",
        "SELL_TO_CLOSE": "EXIT_LONG",
        "CLOSE_SHORT": "EXIT_SHORT",
        "BUY_TO_COVER": "EXIT_SHORT"
    }
    return aliases.get(clean, clean)


def first_payload_value(payload, keys, default=None):
    for key in keys:
        if isinstance(payload, dict) and payload.get(key) not in (None, ""):
            return payload.get(key)
    return default


def first_payload_float(payload, keys, default=None):
    value = first_payload_value(payload, keys, None)
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_tradingview_execution_signal(payload):
    payload = payload if isinstance(payload, dict) else {}
    action = normalize_tradingview_action(first_payload_value(payload, ["action", "side", "signal"], ""))
    symbol = str(first_payload_value(payload, ["ticker", "symbol", "contract"], "")).upper().strip()
    price = first_payload_float(payload, ["price", "close", "mark", "entry"], None)
    edge = first_payload_float(payload, ["edge", "score"], 0)
    qty = env_int(TRADOVATE_DEFAULT_ORDER_QTY_ENV, 1)
    payload_qty = first_payload_float(payload, ["qty", "quantity", "orderQty", "contracts"], None)
    if payload_qty is not None:
        qty = int(payload_qty)
    qty = max(1, min(qty, env_int(TRADOVATE_MAX_ORDER_QTY_ENV, 1)))

    target = first_payload_float(payload, ["target", "take_profit", "takeProfit", "tp"], None)
    stop = first_payload_float(payload, ["stop", "stop_loss", "stopLoss", "sl"], None)
    target_pct = first_payload_float(payload, ["target_pct", "targetPct"], env_float(ALGO_DEFAULT_TARGET_PCT_ENV, 0.02))
    stop_pct = first_payload_float(payload, ["stop_pct", "stopPct"], env_float(ALGO_DEFAULT_STOP_PCT_ENV, 0.01))

    if price and action in {"BUY", "SELL"}:
        if target is None:
            target = price * (1 + target_pct) if action == "BUY" else price * (1 - target_pct)
        if stop is None:
            stop = price * (1 - stop_pct) if action == "BUY" else price * (1 + stop_pct)

    signal_key = str(first_payload_value(payload, ["trade_id", "id", "bar_time", "time"], "")).strip()
    if not signal_key:
        signal_key = str(int(time.time() // 60))

    return {
        "action": action,
        "symbol": symbol,
        "tradovate_symbol": normalize_tradovate_symbol(symbol),
        "price": round(price, 4) if price else None,
        "edge": round(edge, 2),
        "qty": qty,
        "target": round(target, 4) if target else None,
        "stop": round(stop, 4) if stop else None,
        "tag": str(first_payload_value(payload, ["tag"], "ai-algo")),
        "signal_key": f"{symbol}:{action}:{signal_key}"
    }


def duplicate_tradingview_signal(signal, ttl=90):
    now = time.time()
    for key, timestamp in list(tradingview_recent_signal_keys.items()):
        if now - timestamp > ttl:
            tradingview_recent_signal_keys.pop(key, None)

    key = signal.get("signal_key")
    if key in tradingview_recent_signal_keys:
        return True
    tradingview_recent_signal_keys[key] = now
    return False


def count_today_tradovate_routes():
    today = datetime.now(DEMO_TIMEZONE).date().isoformat()
    count = 0
    for message in tradingview_alert_messages:
        execution = message.get("execution") or {}
        if execution.get("routed") and str(message.get("received_at", "")).startswith(today):
            count += 1
    return count


def post_tradovate_order(endpoint, body):
    response = requests.post(
        f"{get_tradovate_rest_url()}/order/{endpoint}",
        headers=tradovate_headers(),
        json=body,
        timeout=12
    )
    if response.status_code == 404 and endpoint == "placeoso":
        response = requests.post(
            f"{get_tradovate_rest_url()}/order/placeOSO",
            headers=tradovate_headers(),
            json=body,
            timeout=12
        )
    response.raise_for_status()
    data = response.json()
    failure = data.get("failureReason") if isinstance(data, dict) else None
    return {
        "ok": failure in (None, "", "Success"),
        "response": data,
        "failure": failure,
        "failure_text": data.get("failureText") if isinstance(data, dict) else None
    }


def place_tradovate_market_order(signal, account):
    action = "Sell" if signal["action"] == "EXIT_LONG" else "Buy"
    body = {
        "accountSpec": account["accountSpec"],
        "accountId": account["accountId"],
        "action": action,
        "symbol": signal["tradovate_symbol"],
        "orderQty": signal["qty"],
        "orderType": "Market",
        "isAutomated": True,
        "customTag50": signal.get("tag", "ai-algo")[:64]
    }
    return post_tradovate_order("placeorder", body)


def place_tradovate_bracket_order(signal, account):
    entry_action = "Buy" if signal["action"] == "BUY" else "Sell"
    exit_action = "Sell" if signal["action"] == "BUY" else "Buy"
    if not signal.get("target") or not signal.get("stop"):
        raise RuntimeError("TradingView alert needs a target and stop before routing a bracket order.")

    body = {
        "accountSpec": account["accountSpec"],
        "accountId": account["accountId"],
        "action": entry_action,
        "symbol": signal["tradovate_symbol"],
        "orderQty": signal["qty"],
        "orderType": "Market",
        "isAutomated": True,
        "customTag50": signal.get("tag", "ai-algo")[:64],
        "bracket1": {
            "action": exit_action,
            "orderType": "Limit",
            "price": signal["target"]
        },
        "bracket2": {
            "action": exit_action,
            "orderType": "Stop",
            "stopPrice": signal["stop"]
        }
    }
    return post_tradovate_order("placeoso", body)


def route_tradingview_signal_to_tradovate(payload):
    signal = build_tradingview_execution_signal(payload)
    result = {
        "enabled": tradovate_auto_trade_enabled(),
        "ready": tradovate_execution_ready(),
        "routed": False,
        "signal": signal,
        "reason": ""
    }

    if signal["action"] not in {"BUY", "SELL", "EXIT_LONG", "EXIT_SHORT"}:
        result["reason"] = "TradingView alert was stored but action is not routable."
        return result
    if not signal["symbol"]:
        result["reason"] = "TradingView alert was stored but no ticker/contract was supplied."
        return result
    if not tradovate_configured():
        result["reason"] = "Tradovate credentials are not configured."
        return result
    if not tradovate_auto_trade_enabled():
        result["reason"] = "Auto-trading is off. Set TRADOVATE_AUTO_TRADE_ENABLED=true after demo testing."
        return result
    if not live_trading_acknowledged():
        result["reason"] = "Live Tradovate orders require TRADOVATE_LIVE_TRADING_ACK=I_UNDERSTAND_REAL_MONEY_RISK."
        return result
    if duplicate_tradingview_signal(signal):
        result["reason"] = "Duplicate TradingView signal ignored."
        return result

    max_daily_orders = env_int(TRADOVATE_MAX_DAILY_ORDERS_ENV, 5)
    if count_today_tradovate_routes() >= max_daily_orders:
        result["reason"] = f"Daily Tradovate order cap reached ({max_daily_orders})."
        return result

    if signal["action"] in {"BUY", "SELL"}:
        min_edge = env_float(ALGO_MIN_EDGE_FOR_AUTO_TRADE_ENV, 18)
        if abs(signal.get("edge") or 0) < min_edge:
            result["reason"] = f"Signal edge is below auto-trade minimum ({min_edge})."
            return result
        if not signal.get("price"):
            result["reason"] = "Entry alerts need a live TradingView price."
            return result

    try:
        account = resolve_tradovate_account()
        order_result = (
            place_tradovate_bracket_order(signal, account)
            if signal["action"] in {"BUY", "SELL"}
            else place_tradovate_market_order(signal, account)
        )
        result.update({
            "routed": bool(order_result.get("ok")),
            "account": {
                "accountSpec": account.get("accountSpec"),
                "accountId": account.get("accountId")
            },
            "order": order_result.get("response"),
            "failure": order_result.get("failure"),
            "reason": order_result.get("failure_text") or ("Tradovate order routed." if order_result.get("ok") else "Tradovate rejected the order.")
        })
    except Exception as exc:
        result["reason"] = str(exc)

    return result


def fetch_polygon_candles(symbol, tf):
    api_key = get_polygon_api_key()
    if not api_key:
        return None

    config = get_polygon_range_config(tf)
    end_date = datetime.now(tz=DEMO_TIMEZONE).date()
    start_date = end_date.fromordinal(end_date.toordinal() - config["days"])

    response = requests.get(
        f"{POLYGON_BASE_URL}/v2/aggs/ticker/{symbol}/range/{config['multiplier']}/{config['timespan']}/{start_date.isoformat()}/{end_date.isoformat()}",
        params={
            "adjusted": "true",
            "sort": "asc",
            "limit": 5000,
            "apiKey": api_key
        },
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()

    results = payload.get("results") or []
    if not results:
        return None

    candles = []
    for row in results:
        open_price = float(row.get("o", 0))
        close_price = float(row.get("c", 0))
        high_price = float(row.get("h", max(open_price, close_price)))
        low_price = float(row.get("l", min(open_price, close_price)))
        candles.append({
            "time": parse_polygon_timestamp(row.get("t")),
            "open": round(open_price, 2),
            "high": round(max(high_price, open_price, close_price), 2),
            "low": round(min(low_price, open_price, close_price), 2),
            "close": round(close_price, 2),
            "volume": int(row.get("v", 0))
        })

    return candles


def fetch_polygon_price(symbol):
    api_key = get_polygon_api_key()
    if not api_key:
        return None

    response = requests.get(
        f"{POLYGON_BASE_URL}/v2/last/trade/{symbol}",
        params={"apiKey": api_key},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    trade = payload.get("results") or {}
    price = trade.get("p")
    if price is None:
        return None
    return round(float(price), 2)


def fetch_polygon_previous_close_quote(symbol):
    api_key = get_polygon_api_key()
    if not api_key:
        return None

    response = requests.get(
        f"{POLYGON_BASE_URL}/v2/aggs/ticker/{symbol}/prev",
        params={
            "adjusted": "true",
            "apiKey": api_key
        },
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    results = payload.get("results") or []
    if not results:
        return None

    row = results[0]
    open_price = float(row.get("o", 0))
    close_price = float(row.get("c", 0))
    high_price = float(row.get("h", max(open_price, close_price)))
    low_price = float(row.get("l", min(open_price, close_price)))
    return {
        "price": round(close_price, 2),
        "open": round(open_price, 2),
        "high": round(max(high_price, open_price, close_price), 2),
        "low": round(min(low_price, open_price, close_price), 2)
    }


def get_previous_close_quote(symbol):
    cache_key = f"prev_close:{symbol}"
    cached = get_cache_entry(quote_cache, cache_key, PREVIOUS_CLOSE_CACHE_TTL)
    if cached and not cached["stale"]:
        return {
            "data": cached["data"],
            "source": "cache",
            "cached": True,
            "stale": False,
            "age_seconds": cached["age_seconds"]
        }

    try:
        quote_data = fetch_polygon_previous_close_quote(symbol)
        if quote_data:
            set_cache_entry(quote_cache, cache_key, quote_data)
            set_cache_entry(quote_cache, symbol, quote_data)
            return {
                "data": quote_data,
                "source": "live",
                "cached": False,
                "stale": False,
                "age_seconds": 0
            }
    except requests.RequestException:
        pass

    if cached:
        return {
            "data": cached["data"],
            "source": "cache",
            "cached": True,
            "stale": True,
            "age_seconds": cached["age_seconds"]
        }

    return None


def search_polygon_symbols(query):
    api_key = get_polygon_api_key()
    if not api_key or len(query.strip()) < 1:
        return []

    response = requests.get(
        f"{POLYGON_BASE_URL}/v3/reference/tickers",
        params={
            "search": query.strip(),
            "market": "stocks",
            "active": "true",
            "limit": 8,
            "apiKey": api_key
        },
        timeout=15
    )
    response.raise_for_status()
    payload = response.json()
    matches = payload.get("results") or []
    results = []
    normalized_query = query.strip().upper()

    for item in matches:
        symbol = str(item.get("ticker") or "").strip().upper()
        name = str(item.get("name") or "").strip()
        market = str(item.get("market") or "").strip().lower()
        type_label = str(item.get("type") or "").strip().lower()

        if not symbol or not name:
            continue
        if market and market != "stocks":
            continue
        if type_label and type_label not in {"cs", "common_stock", "adr", "etf"}:
            continue

        exact_symbol = symbol == normalized_query
        starts_symbol = symbol.startswith(normalized_query)
        starts_name = name.upper().startswith(normalized_query)
        contains_name = normalized_query in name.upper()

        score = 0
        if exact_symbol:
            score += 100
        if starts_symbol:
            score += 30
        if starts_name:
            score += 20
        if contains_name:
            score += 10

        results.append({
            "symbol": symbol,
            "name": name,
            "exchange": str(item.get("primary_exchange") or item.get("locale") or "").strip(),
            "country": "USA",
            "instrument_type": "stock",
            "score": score
        })

    results.sort(key=lambda item: (-item["score"], item["symbol"]))
    return results[:8]


def search_symbols(query):
    if not get_polygon_api_key() or len(query.strip()) < 1:
        return []

    try:
        return search_polygon_symbols(query)
    except Exception as exc:
        print("Polygon symbol search error:", exc)
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
        candle_rows = fetch_tradovate_candles(symbol, tf) if tradovate_configured() else None
        source = "tradovate" if candle_rows else None
        if not candle_rows:
            candle_rows = fetch_polygon_candles(symbol, tf) if get_polygon_api_key() else None
            source = "polygon" if candle_rows else None

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
            set_cache_entry(quote_cache, symbol, build_latest_session_quote(candle_rows))

        return {
            "candles": candle_rows,
            "source": source or "live",
            "cached": False,
            "stale": False,
            "age_seconds": 0
        }
    except Exception as exc:
        print("Market candle fetch error:", exc)
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
        live_price = fetch_polygon_price(symbol) if get_polygon_api_key() else None
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
    if not candles:
        return {
            "action": "WAIT",
            "tone": "neutral",
            "grade": "C",
            "strength": "Weak",
            "confirmations": [],
            "reason": "There is not enough market data yet to build a reliable setup.",
            "score": 0,
            "confidence": 35,
            "setup_quality": "Needs patience",
            "algorithm": {
                "long_score": 0,
                "short_score": 0,
                "edge": 0,
                "relative_volume": 1,
                "risk_flags": ["not enough market data"]
            }
        }

    closes = [float(c["close"]) for c in candles]
    volumes = [float(c.get("volume") or 0) for c in candles]
    ema9 = calculate_ema(closes, 9)
    ema20 = calculate_ema(closes, 20)
    vwap = calculate_vwap(candles)
    rsi = calculate_rsi(closes, 14)

    last_close = closes[-1] if closes else 0
    last_ema9 = ema9[-1] if ema9 else last_close
    last_ema20 = ema20[-1] if ema20 else last_close
    last_vwap = vwap[-1] if vwap else last_close
    last_rsi = rsi[-1] if rsi and rsi[-1] is not None else 50
    recent_candles = candles[-8:] if len(candles) >= 8 else candles
    prior_recent_candles = candles[-9:-1] if len(candles) >= 9 else candles[:-1]
    recent_high = max(float(c["high"]) for c in prior_recent_candles) if prior_recent_candles else last_close
    recent_low = min(float(c["low"]) for c in prior_recent_candles) if prior_recent_candles else last_close
    recent_volume = average(volumes[-6:] or volumes)
    baseline_volume = average(volumes[:-6] or volumes)
    relative_volume = round(recent_volume / baseline_volume, 2) if baseline_volume else 1
    average_range = average([(float(c["high"]) - float(c["low"])) / max(float(c["close"]), 1) for c in recent_candles])
    green_count = sum(1 for c in recent_candles if float(c["close"]) >= float(c["open"]))
    red_count = len(recent_candles) - green_count
    first_recent_close = float(recent_candles[0]["close"]) if recent_candles else last_close
    recent_move = ((last_close - first_recent_close) / first_recent_close) * 100 if first_recent_close else 0
    higher_lows = len(recent_candles) >= 3 and float(recent_candles[-1]["low"]) >= float(recent_candles[-3]["low"])
    lower_highs = len(recent_candles) >= 3 and float(recent_candles[-1]["high"]) <= float(recent_candles[-3]["high"])

    long_score = 35.0
    short_score = 35.0
    bullish_confirmations = []
    bearish_confirmations = []
    risk_flags = []

    if last_close > last_ema9 > last_ema20:
        long_score += 16
        bullish_confirmations.append("trend is stacked bullish above EMA 9 and EMA 20")
    elif last_close < last_ema9 < last_ema20:
        short_score += 16
        bearish_confirmations.append("trend is stacked bearish below EMA 9 and EMA 20")
    elif last_close > last_ema20:
        long_score += 7
        bullish_confirmations.append("price is holding above the medium trend")
    elif last_close < last_ema20:
        short_score += 7
        bearish_confirmations.append("price is under the medium trend")

    if last_close > last_vwap:
        long_score += 8
        bullish_confirmations.append("buyers are defending VWAP")
    elif last_close < last_vwap:
        short_score += 8
        bearish_confirmations.append("sellers are controlling VWAP")

    if last_rsi >= 58:
        long_score += clamp((last_rsi - 50) * 0.65, 4, 16)
        bullish_confirmations.append(f"RSI momentum is firm at {round(last_rsi, 1)}")
    elif last_rsi <= 42:
        short_score += clamp((50 - last_rsi) * 0.65, 4, 16)
        bearish_confirmations.append(f"RSI momentum is weak at {round(last_rsi, 1)}")

    if recent_move >= 0.4 or green_count >= max(3, len(recent_candles) - 2):
        long_score += 10
        bullish_confirmations.append("recent candles are pushing higher")
    elif recent_move <= -0.4 or red_count >= max(3, len(recent_candles) - 2):
        short_score += 10
        bearish_confirmations.append("recent candles are pressing lower")

    if higher_lows:
        long_score += 8
        bullish_confirmations.append("structure is forming higher lows")
    if lower_highs:
        short_score += 8
        bearish_confirmations.append("structure is forming lower highs")

    if recent_high and last_close > recent_high:
        long_score += 12
        bullish_confirmations.append("price is breaking above recent resistance")
    if recent_low and last_close < recent_low:
        short_score += 12
        bearish_confirmations.append("price is breaking below recent support")

    if change >= 0.5:
        long_score += clamp(change * 1.8, 3, 14)
        bullish_confirmations.append(f"the day change is positive at {change}%")
    elif change <= -0.5:
        short_score += clamp(abs(change) * 1.8, 3, 14)
        bearish_confirmations.append(f"the day change is negative at {abs(change)}%")

    if relative_volume >= 1.35:
        if long_score >= short_score:
            long_score += 11
            bullish_confirmations.append(f"volume is confirming at {relative_volume}x normal")
        else:
            short_score += 11
            bearish_confirmations.append(f"volume is confirming at {relative_volume}x normal")
    elif relative_volume < 0.65:
        risk_flags.append("volume is too light")
        long_score -= 5
        short_score -= 5

    if average_range < 0.0025:
        risk_flags.append("price action is too tight and choppy")
        long_score -= 6
        short_score -= 6

    if last_rsi >= 78:
        risk_flags.append("bullish side is overextended")
        long_score -= 10
        short_score += 4
    elif last_rsi <= 22:
        risk_flags.append("bearish side is overextended")
        short_score -= 10
        long_score += 4

    if strategy in {"momentum", "day", "scalp"}:
        if relative_volume >= 1.1 and abs(recent_move) >= 0.3:
            long_score += 4 if long_score > short_score else 0
            short_score += 4 if short_score > long_score else 0
        if relative_volume < 0.8:
            risk_flags.append("momentum strategy needs more volume")
    elif strategy == "swing":
        if last_close > last_ema20:
            long_score += 4
        elif last_close < last_ema20:
            short_score += 4
    elif strategy == "mean":
        if last_rsi <= 32 and last_close < last_ema20:
            long_score += 12
            bullish_confirmations.append("mean reversion is stretched lower")
        elif last_rsi >= 68 and last_close > last_ema20:
            short_score += 12
            bearish_confirmations.append("mean reversion is stretched higher")

    long_score = round(clamp(long_score, 0, 100), 1)
    short_score = round(clamp(short_score, 0, 100), 1)
    edge = round(long_score - short_score, 1)
    dominant_score = max(long_score, short_score)
    opposing_score = min(long_score, short_score)
    hard_guardrail = len(risk_flags) >= 2 and abs(edge) < 35

    if edge >= 18 and long_score >= 58 and not hard_guardrail:
        action = "BUY"
        tone = "bullish"
        confirmations = bullish_confirmations
    elif edge <= -18 and short_score >= 58 and not hard_guardrail:
        action = "SELL"
        tone = "bearish"
        confirmations = bearish_confirmations
    else:
        action = "WAIT"
        tone = "neutral"
        confirmations = bullish_confirmations if edge >= 0 else bearish_confirmations

    if action != "WAIT" and abs(edge) >= 32 and dominant_score >= 72 and len(confirmations) >= 4 and not risk_flags:
        grade = "A"
        strength = "Strong"
    elif action != "WAIT" and abs(edge) >= 18 and dominant_score >= 58:
        grade = "B"
        strength = "Moderate"
    elif abs(edge) >= 14 and confirmations:
        grade = "C"
        strength = "Mixed"
    else:
        grade = "C"
        strength = "Weak"

    if action == "BUY":
        reason = "Algorithm says BUY only because multiple bullish checks line up: " + ", ".join(confirmations[:3]) + "."
    elif action == "SELL":
        reason = "Algorithm says SELL only because multiple bearish checks line up: " + ", ".join(confirmations[:3]) + "."
    elif confirmations:
        reason = "The setup is close, but not strong enough yet. Best evidence so far: " + ", ".join(confirmations[:2]) + "."
    else:
        reason = "This is a WAIT because trend, momentum, volume, and structure are not lined up enough yet."

    if risk_flags:
        reason += " Caution: " + ", ".join(risk_flags[:2]) + "."

    return {
        "action": action,
        "tone": tone,
        "grade": grade,
        "strength": strength,
        "confirmations": confirmations[:5],
        "reason": reason,
        "score": dominant_score,
        "confidence": round(clamp(45 + (abs(edge) * 0.8) + ((relative_volume - 1) * 7) - (len(risk_flags) * 7), 35, 95)),
        "setup_quality": (
            "High quality" if grade == "A" and action != "WAIT"
            else "Usable" if grade == "B" and action != "WAIT"
            else "Needs patience"
        ),
        "algorithm": {
            "long_score": long_score,
            "short_score": short_score,
            "edge": edge,
            "relative_volume": relative_volume,
            "rsi": round(last_rsi, 1),
            "recent_move": round(recent_move, 2),
            "risk_flags": risk_flags,
            "dominant_score": dominant_score,
            "opposing_score": opposing_score
        }
    }


def average(values):
    cleaned = [float(value) for value in values if value is not None]
    return sum(cleaned) / len(cleaned) if cleaned else 0


def clamp(value, low, high):
    return max(low, min(high, value))


def describe_social_signal(headlines):
    joined = " ".join(headlines).lower()
    social_terms = ["reddit", "wallstreetbets", "social", "retail", "meme", "x.com", "twitter"]
    social_hits = sum(1 for term in social_terms if term in joined)
    if social_hits >= 2:
        return {
            "label": "Social chatter is elevated",
            "strength": "high",
            "detail": "Headline flow includes message-board or retail-trader language."
        }
    if social_hits == 1:
        return {
            "label": "Some social chatter is showing up",
            "strength": "moderate",
            "detail": "There is at least one headline hinting at social or retail attention."
        }
    return {
        "label": "No clear social driver",
        "strength": "low",
        "detail": "This is not coming from direct social scraping, and current headlines do not suggest a strong social catalyst."
    }


def build_why_moving_engine(symbol, price, change, candles, news, events):
    headlines = [article.get("title", "") for article in (news or {}).get("articles", [])]
    volumes = [float(candle.get("volume") or 0) for candle in candles[-30:]]
    recent_volumes = volumes[-6:] or volumes
    baseline_volumes = volumes[:-6] or volumes
    average_recent_volume = average(recent_volumes)
    average_baseline_volume = average(baseline_volumes)
    relative_volume = round(average_recent_volume / average_baseline_volume, 2) if average_baseline_volume else 1.0
    price_action = "up" if change > 0 else "down" if change < 0 else "flat"
    news_impact = (news or {}).get("impact") or {}
    social_signal = describe_social_signal(headlines)
    earnings = (events or {}).get("earnings") or {}
    next_earnings = earnings.get("next_earnings_date")
    news_catalyst = news.get("driver") if isinstance(news, dict) else "Headline catalyst is unavailable."

    drivers = []
    if relative_volume >= 1.8:
        drivers.append(f"volume is running at about {relative_volume}x its recent pace")
    elif relative_volume >= 1.25:
        drivers.append(f"volume is a bit elevated at about {relative_volume}x normal")

    if headlines:
        drivers.append(news_catalyst.replace("Possible reason it's up: ", "").replace("Possible reason it's down: ", "").replace("Possible driver today: ", "").rstrip("."))

    if news_impact.get("score", 0) >= 2:
        drivers.append("headline tone is leaning bullish")
    elif news_impact.get("score", 0) <= -2:
        drivers.append("headline tone is leaning bearish")

    if social_signal["strength"] in {"high", "moderate"}:
        drivers.append(social_signal["label"].lower())

    if next_earnings:
        next_dt = parse_event_datetime(next_earnings)
        if next_dt:
            days_until = (next_dt.date() - datetime.now(DEMO_TIMEZONE).date()).days
            if 0 <= days_until <= 7:
                drivers.append(f"earnings are coming up in {days_until} day{'s' if days_until != 1 else ''}")

    if not drivers:
        drivers.append("price is moving, but no single catalyst is standing out yet")

    explanation = f"{symbol} is {price_action} {abs(change):.2f}% today because " + ", ".join(drivers[:3]) + "."
    return {
        "summary": explanation,
        "price_action": price_action,
        "relative_volume": relative_volume,
        "news_catalyst": news_catalyst,
        "social_signal": social_signal,
        "drivers": drivers[:4]
    }


def build_momentum_score(change, candles, news_impact, trade_signal):
    closes = [candle["close"] for candle in candles]
    volumes = [float(candle.get("volume") or 0) for candle in candles]
    ema9 = calculate_ema(closes, 9)
    ema20 = calculate_ema(closes, 20)
    rsi14 = calculate_rsi(closes, 14)
    last_close = closes[-1] if closes else 0
    last_ema9 = ema9[-1] if ema9 else last_close
    last_ema20 = ema20[-1] if ema20 else last_close
    last_rsi = rsi14[-1] if rsi14 and rsi14[-1] is not None else 50
    avg_recent_volume = average(volumes[-6:])
    avg_volume = average(volumes[:-6] or volumes)
    relative_volume = (avg_recent_volume / avg_volume) if avg_volume else 1

    score = 50
    score += clamp(change * 4, -20, 20)
    score += 10 if last_close > last_ema9 else -10
    score += 10 if last_close > last_ema20 else -10
    score += clamp((last_rsi - 50) * 0.6, -12, 12)
    score += clamp((relative_volume - 1) * 18, -8, 18)
    score += clamp((news_impact or {}).get("score", 0) * 4, -10, 10)
    score += {"A": 10, "B": 4, "C": -4}.get((trade_signal or {}).get("grade"), 0)
    final_score = int(round(clamp(score, 1, 100)))

    if final_score >= 80:
        label = "Explosive"
    elif final_score >= 65:
        label = "Strong"
    elif final_score >= 45:
        label = "Balanced"
    else:
        label = "Weak"

    return {
        "value": final_score,
        "label": label,
        "summary": f"Momentum score is {final_score}/100, driven by price trend, volume pressure, news tone, and current setup quality."
    }


def detect_market_mode(change, candles):
    closes = [candle["close"] for candle in candles]
    volumes = [float(candle.get("volume") or 0) for candle in candles]
    ema9 = calculate_ema(closes, 9)
    ema20 = calculate_ema(closes, 20)
    last_close = closes[-1] if closes else 0
    last_ema9 = ema9[-1] if ema9 else last_close
    last_ema20 = ema20[-1] if ema20 else last_close
    range_values = [(candle["high"] - candle["low"]) for candle in candles[-8:]]
    avg_range = average(range_values)
    avg_price = average(closes[-8:]) or last_close or 1
    relative_range = avg_range / avg_price if avg_price else 0
    recent_volume = average(volumes[-6:])
    baseline_volume = average(volumes[:-6] or volumes)
    relative_volume = recent_volume / baseline_volume if baseline_volume else 1

    if last_close > last_ema9 > last_ema20 and change >= 0.75:
        mode = "Bullish Trend"
        note = "Trend is aligned higher and buyers are keeping price above the fast averages."
    elif last_close < last_ema9 < last_ema20 and change <= -0.75:
        mode = "Bearish Trend"
        note = "Trend is aligned lower and sellers are keeping price below the fast averages."
    elif relative_range < 0.0035 and relative_volume < 0.9:
        mode = "Choppy"
        note = "Range and participation both look soft, so follow-through risk is lower."
    elif relative_volume >= 1.5 and abs(change) >= 1:
        mode = "Expansion"
        note = "Participation is elevated and the stock is stretching away from its baseline."
    else:
        mode = "Balanced"
        note = "The tape is active enough to trade, but the trend is not dominant yet."

    return {
        "label": mode,
        "summary": note
    }


def build_trade_warning(change, candles, momentum_score):
    closes = [candle["close"] for candle in candles]
    volumes = [float(candle.get("volume") or 0) for candle in candles]
    ranges = [float(candle["high"] - candle["low"]) for candle in candles[-10:]]
    avg_range = average(ranges)
    avg_price = average(closes[-10:]) or 1
    relative_range = avg_range / avg_price if avg_price else 0
    relative_volume = average(volumes[-6:]) / (average(volumes[:-6] or volumes) or 1)
    overextended = abs(change) >= 6 or momentum_score["value"] >= 88

    warnings = []
    if relative_volume < 0.8:
        warnings.append("volume is light")
    if relative_range < 0.0025:
        warnings.append("the chart is choppy")
    if overextended:
        warnings.append("the move is already stretched")

    if not warnings:
        return {
            "label": "Trade is not blocked",
            "tone": "ok",
            "summary": "Nothing major is flashing red right now, so trade quality depends on execution and risk control."
        }

    return {
        "label": "Don’t chase this blindly",
        "tone": "warning",
        "summary": "Be careful here because " + ", ".join(warnings[:3]) + "."
    }


def build_position_size_guide(entry, stop):
    risk_per_share = abs(entry - stop)
    return {
        "entry": round(entry, 2),
        "stop": round(stop, 2),
        "risk_per_share": round(risk_per_share, 2)
    }


def build_broker_readiness():
    return {
        "status": "Broker not connected",
        "summary": "Live execution is still locked until a supported broker, funding flow, and compliance checks are connected.",
        "steps": [
            "Connect a supported broker account",
            "Finish funding and identity verification",
            "Enable live order routing and trade confirmations"
        ]
    }


def build_ai_trade_setup(symbol, strategy, risk_profile, trade_signal, plan, why_moving, market_mode, momentum_score, trade_warning):
    reward = abs((plan["targets"][0] if plan["targets"] else plan["entry"]) - plan["entry"])
    risk = abs(plan["entry"] - plan["stop"]) or 0.01
    rr = round(reward / risk, 2) if risk else 0
    caution = trade_warning.get("tone") == "warning"

    if trade_signal["action"] == "WAIT":
        stance = "Wait for better confirmation"
    elif caution and risk_profile == "conservative":
        stance = "Reduce size or wait for a cleaner retest"
    else:
        stance = f"{trade_signal['action']} bias is acceptable for a {risk_profile} {strategy} trader"

    reasoning = [
        why_moving.get("summary"),
        market_mode.get("summary"),
        f"The current setup grade is {trade_signal.get('grade')} with {trade_signal.get('strength', '').lower()} conviction.",
        f"First target offers about {rr}:1 reward-to-risk."
    ]
    if caution:
        reasoning.append(trade_warning.get("summary"))

    return {
        "risk_profile": risk_profile,
        "stance": stance,
        "entry": round(plan["entry"], 2),
        "stop": round(plan["stop"], 2),
        "target": round(plan["targets"][0], 2) if plan["targets"] else round(plan["entry"], 2),
        "reward_to_risk": rr,
        "reasoning": reasoning[:4],
        "trigger": (
            f"Act only if price confirms through {round(plan['entry'], 2)} with follow-through."
            if trade_signal.get("action") != "WAIT"
            else "Wait for a cleaner confirmation before acting."
        ),
        "invalidation": f"Step aside if price loses {round(plan['stop'], 2)} or momentum fades."
    }


def build_smart_alert_ideas(symbol, trade_signal, momentum_score, levels, market_mode):
    buy_level = round(float(levels.get("resistance") or 0), 2)
    sell_level = round(float(levels.get("support") or 0), 2)
    action = trade_signal.get("action")
    grade = trade_signal.get("grade", "C")
    market_label = market_mode.get("label", "current")
    momentum_trigger = min(95, max(60, momentum_score["value"] + 8))

    buy_alert = {
        "label": f"BUY alert if {symbol} breaks above ${buy_level}",
        "detail": f"Use this only if the breakout holds with the current {grade} setup and {market_label} tape.",
        "priority": "High" if action == "BUY" else "Watch",
        "type": "price_above",
        "target": buy_level,
        "side": "BUY",
        "why_now": trade_signal.get("reason")
    }
    sell_alert = {
        "label": f"SELL alert if {symbol} loses ${sell_level}",
        "detail": f"Use this as a breakdown or protection alert if buyers fail at support.",
        "priority": "High" if action == "SELL" else "Risk",
        "type": "price_below",
        "target": sell_level,
        "side": "SELL",
        "why_now": trade_signal.get("reason")
    }
    momentum_alert = {
        "label": f"Momentum score pushes past {momentum_trigger}",
        "detail": "That would mean stronger continuation pressure instead of a random price tick.",
        "priority": "Medium",
        "type": "momentum_score",
        "target": momentum_trigger,
        "side": action if action in {"BUY", "SELL"} else "WATCH",
        "why_now": momentum_score.get("summary")
    }

    if action == "SELL":
        return [sell_alert, buy_alert, momentum_alert]
    return [buy_alert, sell_alert, momentum_alert]


def build_scanner_row(symbol):
    snapshot = get_watchlist_snapshot(symbol)
    if not snapshot:
        return None

    candle_result = fetch_and_cache_candles(symbol, "5m")
    candles = candle_result["candles"] if candle_result and candle_result["candles"] else build_demo_candles(symbol, "5m")
    change = snapshot["change"]
    bias = "Bullish" if change > 0 else "Bearish" if change < 0 else "Neutral"
    trade_signal = build_trade_signal(change, bias, "momentum", candles)
    news = fetch_stock_news(symbol, change)
    events = fetch_market_events(symbol)
    news["impact"] = build_news_impact(
        [article.get("title", "") for article in news.get("articles", [])],
        events.get("earnings") if isinstance(events, dict) else {}
    )
    why_moving = build_why_moving_engine(symbol, snapshot["price"], change, candles, news, events)
    momentum = build_momentum_score(change, candles, news.get("impact"), trade_signal)
    market_mode = detect_market_mode(change, candles)

    continuation_probability = int(clamp(
        momentum["value"] + (8 if trade_signal["action"] != "WAIT" else -8) + (6 if abs(change) >= 2 else 0),
        15,
        95
    ))

    volumes = [float(candle.get("volume") or 0) for candle in candles[-30:]]
    relative_volume = round((average(volumes[-6:]) / (average(volumes[:-6] or volumes) or 1)), 2)

    return {
        "ticker": symbol,
        "price": snapshot["price"],
        "change": change,
        "sparkline": snapshot.get("sparkline") or [],
        "setup_grade": trade_signal["grade"],
        "setup_action": trade_signal["action"],
        "momentum_score": momentum,
        "why_moving": why_moving,
        "market_mode": market_mode,
        "relative_volume": relative_volume,
        "continuation_probability": continuation_probability,
        "mover_tag": (
            "Leader" if continuation_probability >= 80
            else "Actionable" if continuation_probability >= 65
            else "Watching"
        ),
        "unusual_activity": {
            "label": "High" if relative_volume >= 2 else "Elevated" if relative_volume >= 1.3 else "Normal",
            "detail": f"Relative volume is {relative_volume}x versus its recent intraday baseline."
        },
        "trade_signal": trade_signal
    }


def build_algorithm_dashboard(tickers):
    rows = []
    clean_tickers = []
    for ticker in tickers or []:
        symbol = str(ticker or "").upper().strip()
        if symbol and symbol not in clean_tickers:
            clean_tickers.append(symbol)

    if not clean_tickers:
        clean_tickers = load_watchlist() or ALGORITHM_DEFAULT_UNIVERSE

    for ticker in clean_tickers[:10]:
        scanner_row = build_scanner_row(ticker)
        if not scanner_row:
            continue

        signal = scanner_row.get("trade_signal") or {}
        algorithm = signal.get("algorithm") or {}
        action = signal.get("action") or "WAIT"
        direction = 1 if action == "BUY" else -1 if action == "SELL" else 0
        trades = 1 if direction else 0
        signal_move = float(scanner_row.get("change") or 0)
        model_pnl = round((signal_move / 100) * ALGORITHM_SIGNAL_CAPITAL * direction, 2) if trades else 0
        wins = 1 if model_pnl > 0 else 0
        losses = 1 if model_pnl < 0 else 0
        confidence = int(signal.get("confidence") or scanner_row.get("continuation_probability") or 0)
        win_rate = 100 if wins else 0 if trades else 0
        algo_name = f"{ticker} Momentum AI"
        risk_flags = algorithm.get("risk_flags") or []

        rows.append({
            "algo": algo_name,
            "ticker": ticker,
            "action": action,
            "grade": signal.get("grade", "C"),
            "confidence": confidence,
            "trades": trades,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "total_pnl": model_pnl,
            "gross_pnl": model_pnl,
            "net_pnl": model_pnl,
            "best_trade": model_pnl if model_pnl > 0 else 0,
            "worst_trade": model_pnl if model_pnl < 0 else 0,
            "momentum_score": scanner_row.get("momentum_score", {}).get("value", 0),
            "continuation_probability": scanner_row.get("continuation_probability", 0),
            "price": scanner_row.get("price", 0),
            "change": scanner_row.get("change", 0),
            "market_mode": scanner_row.get("market_mode", {}).get("label", "Balanced"),
            "relative_volume": scanner_row.get("relative_volume", 1),
            "reason": signal.get("reason", "Algorithm reason unavailable."),
            "risk_flags": risk_flags,
            "long_score": algorithm.get("long_score", 0),
            "short_score": algorithm.get("short_score", 0),
            "edge": algorithm.get("edge", 0)
        })

    rows.sort(key=lambda item: (item["trades"], item["net_pnl"], item["confidence"], item["momentum_score"]), reverse=True)
    active_rows = [row for row in rows if row["trades"]]
    total_pnl = round(sum(row["net_pnl"] for row in rows), 2)
    total_trades = sum(row["trades"] for row in rows)
    wins = sum(row["wins"] for row in rows)
    best_trade = round(max([row["best_trade"] for row in rows] or [0]), 2)
    worst_trade = round(min([row["worst_trade"] for row in rows] or [0]), 2)
    win_rate = round((wins / total_trades) * 100, 1) if total_trades else 0
    return {
        "date": datetime.now(DEMO_TIMEZONE).strftime("%Y-%m-%d"),
        "generated_at": datetime.now(DEMO_TIMEZONE).isoformat(),
        "mode": "tradingview-signals-tradovate-execution" if tradovate_configured() else "signal-only",
        "live_trading": {
            "enabled": tradovate_execution_ready(),
            "broker_connected": tradovate_configured(),
            "tradovate_configured": tradovate_configured(),
            "tradovate_auto_trade_enabled": tradovate_auto_trade_enabled(),
            "tradovate_environment": get_tradovate_env(),
            "tradingview_webhook_configured": bool(tradingview_webhook_secret()),
            "label": "TradingView live signals + Tradovate execution" if tradovate_configured() else "Live execution locked",
            "summary": (
                "TradingView alerts are the live signal feed. Valid alerts can route bracket orders to Tradovate when auto-trading is enabled."
                if tradovate_execution_ready()
                else "TradingView alerts can be received now. Tradovate order routing stays locked until credentials, account ID, and auto-trade env vars are set."
            )
        },
        "totals": {
            "total_pnl": total_pnl,
            "trades": total_trades,
            "win_rate": win_rate,
            "best_trade": best_trade,
            "worst_trade": worst_trade,
            "active_algos": len(active_rows),
            "tracked_algos": len(rows)
        },
        "rows": rows[:10]
    }


def find_swing_levels(candles, tolerance=0.0035):
    if len(candles) < 7:
        return []

    candidates = []
    for index in range(2, len(candles) - 2):
        candle = candles[index]
        high = candle["high"]
        low = candle["low"]
        if high >= max(c["high"] for c in candles[index - 2:index + 3]):
            candidates.append(("resistance", high))
        if low <= min(c["low"] for c in candles[index - 2:index + 3]):
            candidates.append(("support", low))

    merged = []
    for level_type, price in candidates:
        matched = None
        for item in merged:
            if item["type"] != level_type:
                continue
            if abs(item["price"] - price) / max(price, 1) <= tolerance:
                matched = item
                break
        if matched:
            matched["hits"] += 1
            matched["price"] = round((matched["price"] + price) / 2, 2)
        else:
            merged.append({"type": level_type, "price": round(price, 2), "hits": 1})

    merged.sort(key=lambda item: (item["hits"], item["price"]), reverse=True)
    return merged[:6]


def build_liquidity_map(candles):
    levels = find_swing_levels(candles)
    support = [item for item in levels if item["type"] == "support"][:3]
    resistance = [item for item in levels if item["type"] == "resistance"][:3]
    summary_bits = []
    if resistance:
        summary_bits.append(f"sell-side liquidity may be clustered near {', '.join(str(item['price']) for item in resistance[:2])}")
    if support:
        summary_bits.append(f"buy-side liquidity may be clustered near {', '.join(str(item['price']) for item in support[:2])}")
    return {
        "support": support,
        "resistance": resistance,
        "summary": " and ".join(summary_bits) + "." if summary_bits else "No clear liquidity clusters were found yet."
    }


def pearson_correlation(series_a, series_b):
    length = min(len(series_a), len(series_b))
    if length < 5:
        return 0
    a = series_a[-length:]
    b = series_b[-length:]
    mean_a = average(a)
    mean_b = average(b)
    numerator = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    denominator_a = math.sqrt(sum((x - mean_a) ** 2 for x in a))
    denominator_b = math.sqrt(sum((y - mean_b) ** 2 for y in b))
    if not denominator_a or not denominator_b:
        return 0
    return numerator / (denominator_a * denominator_b)


def build_correlation_tracker(symbol, base_candles):
    closes = [candle["close"] for candle in base_candles]
    peers = [("SPY", "SPY"), ("QQQ", "QQQ"), ("XLK", "XLK")]
    rows = []
    for peer_symbol, label in peers:
        if peer_symbol == symbol:
            continue
        peer_result = fetch_and_cache_candles(peer_symbol, "15m")
        peer_candles = peer_result["candles"] if peer_result and peer_result["candles"] else build_demo_candles(peer_symbol, "15m")
        peer_closes = [candle["close"] for candle in peer_candles]
        corr = pearson_correlation(closes, peer_closes)
        if corr >= 0.6:
            relation = "Strong positive"
        elif corr <= -0.4:
            relation = "Inverse"
        else:
            relation = "Loose"
        rows.append({
            "symbol": label,
            "correlation": round(corr, 2),
            "relation": relation
        })

    rows.sort(key=lambda item: abs(item["correlation"]), reverse=True)
    return {
        "pairs": rows,
        "summary": f"{symbol} is currently most tied to {rows[0]['symbol']}." if rows else "Correlation data is unavailable right now."
    }


def build_squeeze_detector(symbol, change, candles, news, momentum_score):
    volumes = [float(candle.get("volume") or 0) for candle in candles[-30:]]
    relative_volume = average(volumes[-6:]) / (average(volumes[:-6] or volumes) or 1)
    headlines = " ".join(article.get("title", "") for article in news.get("articles", [])).lower()
    social_terms = sum(1 for term in ["reddit", "meme", "retail", "short", "squeeze"] if term in headlines)
    score = 30
    score += clamp(relative_volume * 15, 0, 35)
    score += clamp(abs(change) * 4, 0, 20)
    score += clamp((momentum_score or {}).get("value", 50) * 0.2, 0, 20)
    score += social_terms * 6
    final_score = int(clamp(score, 1, 100))
    if final_score >= 75:
        label = "High squeeze watch"
    elif final_score >= 55:
        label = "Moderate squeeze watch"
    else:
        label = "Low squeeze pressure"
    return {
        "score": final_score,
        "label": label,
        "summary": f"Squeeze score is {final_score}/100, based on expansion in price, relative volume, and social-style headline language."
    }


def build_earnings_volatility_predictor(candles, earnings, momentum_score):
    closes = [candle["close"] for candle in candles]
    atrish = average([(candle["high"] - candle["low"]) for candle in candles[-10:]])
    avg_price = average(closes[-10:]) or closes[-1] if closes else 1
    base_move = (atrish / avg_price) * 100 if avg_price else 0
    next_earnings = parse_event_datetime((earnings or {}).get("next_earnings_date"))
    urgency_boost = 0
    if next_earnings:
        days_until = (next_earnings.date() - datetime.now(DEMO_TIMEZONE).date()).days
        if 0 <= days_until <= 10:
            urgency_boost = max(1, 10 - days_until) * 0.35
    expected_move = round(clamp((base_move * 2.4) + urgency_boost + ((momentum_score.get("value", 50) - 50) * 0.03), 1.2, 18.0), 2)
    return {
        "expected_move_percent": expected_move,
        "summary": f"Expected earnings move is about {expected_move}% based on recent range expansion and event proximity."
    }


def simulate_backtest(candles, strategy):
    if len(candles) < 25:
        return {
            "trades": 0,
            "win_rate": 0,
            "total_return_percent": 0,
            "max_drawdown_percent": 0,
            "summary": "Not enough candles to run a backtest yet."
        }

    closes = [c["close"] for c in candles]
    ema9 = calculate_ema(closes, 9)
    ema20 = calculate_ema(closes, 20)
    position = None
    equity = 10000
    peak = equity
    trades = []

    for index in range(20, len(candles)):
        price = closes[index]
        prev_price = closes[index - 1]
        bullish_cross = prev_price <= ema9[index - 1] and price > ema9[index] and ema9[index] > ema20[index]
        bearish_cross = prev_price >= ema9[index - 1] and price < ema9[index] and ema9[index] < ema20[index]

        if strategy == "mean":
            bullish_cross = price < ema20[index] * 0.992
            bearish_cross = price > ema20[index] * 1.008

        if not position and bullish_cross:
            position = {"side": "BUY", "entry": price}
        elif not position and bearish_cross:
            position = {"side": "SELL", "entry": price}
        elif position:
            exit_trade = False
            if position["side"] == "BUY":
                exit_trade = price < ema20[index] or price >= position["entry"] * 1.02 or price <= position["entry"] * 0.99
                pnl_pct = ((price - position["entry"]) / position["entry"]) * 100
            else:
                exit_trade = price > ema20[index] or price <= position["entry"] * 0.98 or price >= position["entry"] * 1.01
                pnl_pct = ((position["entry"] - price) / position["entry"]) * 100

            if exit_trade:
                trades.append(round(pnl_pct, 2))
                equity *= (1 + (pnl_pct / 100))
                peak = max(peak, equity)
                position = None

    wins = [trade for trade in trades if trade > 0]
    total_return = round(((equity - 10000) / 10000) * 100, 2)
    max_drawdown = round(((peak - equity) / peak) * 100, 2) if peak else 0
    win_rate = round((len(wins) / len(trades)) * 100, 1) if trades else 0
    return {
        "trades": len(trades),
        "win_rate": win_rate,
        "total_return_percent": total_return,
        "max_drawdown_percent": max_drawdown,
        "summary": f"Simple {strategy} backtest found {len(trades)} trades with a {win_rate}% win rate."
    }


def review_closed_trade(trade):
    realized = float(trade.get("realized") or 0)
    entry = float(trade.get("entry") or 0)
    exit_price = float(trade.get("exit") or 0)
    qty = float(trade.get("qty") or 0)
    opened = parse_event_datetime(trade.get("openedAt"))
    closed = parse_event_datetime(trade.get("closedAt"))
    hold_minutes = int(((closed - opened).total_seconds() / 60)) if opened and closed else 0
    note = str(trade.get("note") or "").strip().lower()

    if realized > 0 and hold_minutes >= 30:
        coaching = "You let the trade work instead of forcing a fast exit, which usually helps trend setups."
        grade = "Strong process"
    elif realized < 0 and hold_minutes < 10:
        coaching = "This looks reactive. You may be cutting or chasing too fast before the setup has time to prove itself."
        grade = "Weak process"
    elif realized < 0 and "revenge" in note:
        coaching = "Your note hints at emotional trading. Stepping away after a loss would likely improve your next decision."
        grade = "Emotional risk"
    elif realized > 0 and hold_minutes < 10:
        coaching = "You booked a fast winner. That is fine, but review whether you routinely leave trend continuation on the table."
        grade = "Fast execution"
    else:
        coaching = "This trade was acceptable, but the edge would be stronger with clearer entry timing and a more deliberate exit plan."
        grade = "Average process"

    return {
        "grade": grade,
        "summary": coaching,
        "hold_minutes": hold_minutes,
        "realized": round(realized, 2),
        "ticker": trade.get("ticker"),
        "side": trade.get("side")
    }


def build_trading_coach(history):
    if not history:
        return {
            "summary": "Your coach will start learning once you close a few paper trades.",
            "tips": []
        }

    tips = []
    closed_hours = []
    fast_losses = 0
    losers = 0
    morning_wins = 0
    morning_total = 0

    for trade in history[-30:]:
        review = review_closed_trade(trade)
        closed = parse_event_datetime(trade.get("closedAt"))
        if closed:
            closed_hours.append(closed.hour)
            if closed.hour < 11:
                morning_total += 1
                if float(trade.get("realized") or 0) > 0:
                    morning_wins += 1
        if float(trade.get("realized") or 0) < 0:
            losers += 1
            if review["hold_minutes"] and review["hold_minutes"] < 12:
                fast_losses += 1

    if morning_total >= 3 and morning_wins / morning_total >= 0.6:
        tips.append("You tend to perform better earlier in the session, so your best edge may be in the morning.")
    if losers >= 3 and fast_losses / max(losers, 1) >= 0.5:
        tips.append("A lot of your losses are happening fast. Waiting for cleaner confirmation could improve your results.")
    if closed_hours and average(closed_hours) >= 13:
        tips.append("Your exits skew later in the day. Review whether afternoon trades are helping or just adding noise.")

    if not tips:
        tips.append("Your recent trades are mixed, so focus on repeating the cleanest A and B setups instead of adding more volume.")

    return {
        "summary": tips[0],
        "tips": tips[:3]
    }


def load_following_traders():
    return load_state_list("following_traders")


def save_following_traders(data):
    save_state_list("following_traders", data)


def compute_streaks(history):
    if not history:
        return {
            "current_streak": 0,
            "best_streak": 0,
            "disciplined_days": 0,
            "badges": []
        }

    day_scores = {}
    for trade in history:
        closed = parse_event_datetime(trade.get("closedAt")) or datetime.now(DEMO_TIMEZONE)
        day_key = closed.date().isoformat()
        realized = float(trade.get("realized") or 0)
        entry = float(trade.get("entry") or 0)
        qty = float(trade.get("qty") or 0)
        notional = max(entry * qty, 1)
        risk_ratio = abs(realized) / notional
        disciplined = realized >= 0 or risk_ratio <= 0.0125
        day_scores.setdefault(day_key, []).append(disciplined)

    ordered_days = sorted(day_scores.items())
    current = 0
    best = 0
    disciplined_days = 0
    for _, flags in ordered_days:
        good_day = all(flags)
        if good_day:
            disciplined_days += 1
            current += 1
            best = max(best, current)
        else:
            current = 0

    badges = []
    if disciplined_days >= 3:
        badges.append("3-Day Discipline")
    if disciplined_days >= 5:
        badges.append("Steady Hands")
    if best >= 7:
        badges.append("Weekly Lock-In")

    return {
        "current_streak": current,
        "best_streak": best,
        "disciplined_days": disciplined_days,
        "badges": badges
    }


def get_public_user_rows():
    if not database_enabled:
        return []
    try:
        conn = get_db_connection()
        if not conn:
            return []
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, display_name, COALESCE(public_alias, display_name) AS public_alias
                    FROM users
                    WHERE public_profile = TRUE
                    ORDER BY created_at DESC
                    """
                )
                rows = cursor.fetchall()
        conn.close()
        return rows or []
    except Exception as exc:
        print("Public user query failed:", exc)
        return []


def build_public_leaderboard():
    leaderboard = []
    journals = []
    sentiment = {}
    for user_id, display_name, public_alias in get_public_user_rows():
        history = load_paper_history() if not database_enabled else load_state_list("paper_history", user_id=user_id)
        positions = load_paper_positions() if not database_enabled else load_state_list("paper_positions", user_id=user_id)
        if not history and not positions:
            continue

        realized = sum(float(item.get("realized") or 0) for item in history)
        wins = [item for item in history if float(item.get("realized") or 0) > 0]
        losses = [abs(float(item.get("realized") or 0)) for item in history if float(item.get("realized") or 0) < 0]
        win_rate = round((len(wins) / len(history)) * 100, 1) if history else 0
        profit_factor = round(sum(float(item.get("realized") or 0) for item in wins) / max(sum(losses), 1), 2) if history else 0
        consistency = round((win_rate * 0.55) + (min(profit_factor, 3) * 15), 1)
        score = round(realized * 0.4 + consistency * 3, 1)
        streaks = compute_streaks(history)

        leaderboard.append({
            "user_id": user_id,
            "display_name": public_alias or display_name,
            "realized_pnl": round(realized, 2),
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "consistency": consistency,
            "score": score,
            "streak": streaks["current_streak"]
        })

        for trade in history[:4]:
            journals.append({
                "user_id": user_id,
                "display_name": public_alias or display_name,
                "ticker": trade.get("ticker"),
                "side": trade.get("side"),
                "note": trade.get("note") or "No trade note added.",
                "realized": round(float(trade.get("realized") or 0), 2),
                "closed_at": trade.get("closedAt")
            })

        for position in positions:
            ticker = str(position.get("ticker") or "").upper().strip()
            if not ticker:
                continue
            bucket = sentiment.setdefault(ticker, {"ticker": ticker, "bullish": 0, "bearish": 0, "confidence": 0})
            if position.get("side") == "BUY":
                bucket["bullish"] += 1
            else:
                bucket["bearish"] += 1
            bucket["confidence"] = bucket["bullish"] + bucket["bearish"]

    leaderboard.sort(key=lambda item: (item["score"], item["consistency"], item["realized_pnl"]), reverse=True)
    journals.sort(key=lambda item: item.get("closed_at") or "", reverse=True)
    heatmap = list(sentiment.values())
    heatmap.sort(key=lambda item: item["confidence"], reverse=True)
    return {
        "leaderboard": leaderboard[:12],
        "journals": journals[:16],
        "heatmap": heatmap[:12]
    }


# =========================
# STRATEGY ENGINE
# =========================

def analyze_strategy(symbol, strategy, risk_profile="balanced"):
    market_status = get_current_market_status()
    quote_tf = "1d" if market_status == "Closed" else "5m"
    previous_close_quote = get_previous_close_quote(symbol) if market_status == "Closed" else None
    candle_result = fetch_and_cache_candles(symbol, quote_tf)
    using_demo = not (candle_result and candle_result["candles"])
    signal_candles = candle_result["candles"] if candle_result and candle_result["candles"] else build_demo_candles(symbol, quote_tf)
    quote_data = previous_close_quote["data"] if previous_close_quote else (
        build_quote_from_candles(signal_candles) if quote_tf == "1d" else build_latest_session_quote(signal_candles)
    )
    price = quote_data["price"]
    open_price = quote_data["open"]
    market_source = previous_close_quote["source"] if previous_close_quote else (candle_result["source"] if candle_result else "demo")
    market_cached = previous_close_quote["cached"] if previous_close_quote else (candle_result["cached"] if candle_result else True)
    market_stale = previous_close_quote["stale"] if previous_close_quote else (candle_result["stale"] if candle_result else False)
    market_age = previous_close_quote["age_seconds"] if previous_close_quote else (candle_result["age_seconds"] if candle_result else 0)

    dollar_change = round(price - open_price, 2)
    change = round(((price - open_price) / open_price) * 100, 2) if open_price else 0
    bias = "Bullish" if change > 0 else "Bearish" if change < 0 else "Neutral"
    support = round(price * 0.99, 2)
    resistance = round(price * 1.01, 2)
    trade_signal = build_trade_signal(change, bias, strategy, signal_candles)
    news = fetch_stock_news(symbol, change)
    news["impact"] = build_news_impact(
        [article.get("title", "") for article in news.get("articles", [])],
        {}
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

    plan = {
        "entry": entry,
        "stop": stop,
        "targets": targets
    }
    why_moving = build_why_moving_engine(symbol, price, change, signal_candles, news, {})
    momentum_score = build_momentum_score(change, signal_candles, news.get("impact"), trade_signal)
    market_mode = detect_market_mode(change, signal_candles)
    trade_warning = build_trade_warning(change, signal_candles, momentum_score)
    earnings_volatility = build_earnings_volatility_predictor(signal_candles, {}, momentum_score)
    backtest = simulate_backtest(signal_candles, strategy)
    ai_setup = build_ai_trade_setup(
        symbol,
        strategy,
        risk_profile,
        trade_signal,
        plan,
        why_moving,
        market_mode,
        momentum_score,
        trade_warning
    )
    position_size = build_position_size_guide(entry, stop)
    smart_alerts = build_smart_alert_ideas(symbol, trade_signal, momentum_score, {"support": support, "resistance": resistance}, market_mode)

    return {
        "ticker": symbol,
        "price": price,
        "open_price": open_price,
        "dollar_change": dollar_change,
        "change": change,
        "bias": bias,
        "data_source": market_source,
        "is_demo": using_demo,
        "is_cached": market_cached,
        "is_stale": market_stale,
        "cache_age_seconds": market_age,
        "levels": {
            "support": support,
            "resistance": resistance
        },
        "plan": plan,
        "summary": summary,
        "news": news,
        "why_moving": why_moving,
        "momentum_score": momentum_score,
        "market_mode": market_mode,
        "trade_warning": trade_warning,
        "earnings_volatility": earnings_volatility,
        "backtest": backtest,
        "ai_setup": ai_setup,
        "position_size": position_size,
        "smart_alerts": smart_alerts,
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
        "user": serialize_user(user)
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
        "user": serialize_user(user)
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
    risk_profile = request.args.get("risk", "balanced").strip().lower() or "balanced"

    if not symbol:
        return jsonify({"error": "Missing ticker"}), 400

    result = analyze_strategy(symbol.upper(), strategy, risk_profile)
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


@app.route("/scanner")
def scanner():
    raw_tickers = request.args.get("tickers", "").strip()
    tickers = []
    for ticker in raw_tickers.split(","):
        clean = ticker.upper().strip()
        if clean and clean not in tickers:
            tickers.append(clean)

    if not tickers:
        tickers = load_watchlist()

    rows = []
    for ticker in tickers[:10]:
        row = build_scanner_row(ticker)
        if row:
            rows.append(row)

    rows.sort(key=lambda item: (item["continuation_probability"], item["momentum_score"]["value"], abs(item["change"])), reverse=True)
    hot_list = rows[:5]

    return jsonify({
        "rows": rows,
        "hot_list": hot_list
    })


@app.route("/algorithm-dashboard")
def algorithm_dashboard_route():
    raw_tickers = request.args.get("tickers", "").strip()
    tickers = []
    for ticker in raw_tickers.split(","):
        clean = ticker.upper().strip()
        if clean and clean not in tickers:
            tickers.append(clean)

    return jsonify(build_algorithm_dashboard(tickers))


@app.route("/pine-script")
def pine_script_route():
    from algo_research.pinescript import build_pine_script

    return Response(
        build_pine_script(),
        mimetype="text/plain",
        headers={"Content-Disposition": "inline; filename=ai_algorithm_strategy.pine"}
    )


@app.route("/live-data-status")
def live_data_status_route():
    return jsonify({
        "live_data": "tradingview_webhooks",
        "tradovate_configured": tradovate_configured(),
        "tradovate_environment": get_tradovate_env(),
        "tradovate_auto_trade_enabled": tradovate_auto_trade_enabled(),
        "tradovate_execution_ready": tradovate_execution_ready(),
        "live_trading_acknowledged": live_trading_acknowledged(),
        "tradingview_webhook_configured": bool(tradingview_webhook_secret()),
        "backtesting_data": "databento"
    })


@app.route("/tradingview-webhook", methods=["POST"])
def tradingview_webhook_route():
    expected_secret = tradingview_webhook_secret()
    provided_secret = request.headers.get("X-TradingView-Secret", "") or request.args.get("secret", "")
    if expected_secret and provided_secret != expected_secret:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    payload = request.get_json(silent=True)
    if payload is None:
        raw_body = request.get_data(as_text=True)
        try:
            payload = json.loads(raw_body) if raw_body else {}
        except json.JSONDecodeError:
            payload = {"raw": raw_body}

    message = {
        "received_at": datetime.now(DEMO_TIMEZONE).isoformat(),
        "source": "tradingview",
        "payload": payload,
        "execution": route_tradingview_signal_to_tradovate(payload)
    }
    tradingview_alert_messages.insert(0, message)
    del tradingview_alert_messages[50:]
    return jsonify({"ok": True, "stored": True, "execution": message["execution"]})


@app.route("/tradingview-alerts")
def tradingview_alerts_route():
    return jsonify(tradingview_alert_messages[:25])


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
            "user": serialize_user(current_user),
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
        "user": serialize_user(current_user)
    })


@app.route("/portfolio-insights")
def portfolio_insights():
    history = load_paper_history()
    reviews = [review_closed_trade(item) for item in history[:12]]
    coach = build_trading_coach(history)
    streaks = compute_streaks(history)
    return jsonify({
        "reviews": reviews,
        "coach": coach,
        "streaks": streaks
    })


@app.route("/community")
def community():
    current_user = get_current_user()
    following = load_following_traders()
    payload = build_public_leaderboard()
    payload["following"] = following
    payload["current_user"] = serialize_user(current_user)
    return jsonify(payload)


@app.route("/community/profile", methods=["POST"])
def community_profile():
    current_user = get_current_user()
    if not current_user:
        return jsonify({"error": "Sign in first."}), 401
    payload = request.get_json(silent=True) or {}
    public_alias = str(payload.get("public_alias") or current_user["display_name"]).strip()[:40]
    public_profile = bool(payload.get("public_profile"))
    update_user_profile_fields(current_user["id"], public_profile=public_profile, public_alias=public_alias)
    fresh = get_user_by_id(current_user["id"])
    return jsonify({"ok": True, "user": serialize_user(fresh)})


@app.route("/community/follow", methods=["POST"])
def community_follow():
    current_user = get_current_user()
    if not current_user:
        return jsonify({"error": "Sign in first."}), 401
    payload = request.get_json(silent=True) or {}
    user_id = int(payload.get("user_id") or 0)
    action = str(payload.get("action") or "follow").strip().lower()
    following = [int(item) for item in load_following_traders() if str(item).isdigit()]
    if action == "unfollow":
        following = [item for item in following if item != user_id]
    elif user_id and user_id not in following and user_id != current_user["id"]:
        following.append(user_id)
    save_following_traders(following)
    return jsonify({"ok": True, "following": following})


# =========================
# RUN
# =========================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=True)
