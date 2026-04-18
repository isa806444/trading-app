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
import stripe
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
DATABASE_URL_ENV = "DATABASE_URL"
STRIPE_SECRET_KEY_ENV = "STRIPE_SECRET_KEY"
STRIPE_PUBLISHABLE_KEY_ENV = "STRIPE_PUBLISHABLE_KEY"
STRIPE_PREMIUM_PRICE_CENTS_ENV = "STRIPE_PREMIUM_PRICE_CENTS"
QUOTE_CACHE_TTL = 90
LIVE_PRICE_CACHE_TTL = 5
CANDLE_CACHE_TTL = 180
INDICATOR_CACHE_TTL = 900
NEWS_CACHE_TTL = 900
EVENTS_CACHE_TTL = 1800
DEFAULT_PREMIUM_PRICE_CENTS = 999
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
if os.environ.get(STRIPE_SECRET_KEY_ENV, "").strip():
    stripe.api_key = os.environ.get(STRIPE_SECRET_KEY_ENV, "").strip()


def get_polygon_api_key():
    return os.environ.get(POLYGON_API_KEY_ENV, "").strip()


def get_stripe_secret_key():
    return os.environ.get(STRIPE_SECRET_KEY_ENV, "").strip()


def get_stripe_publishable_key():
    return os.environ.get(STRIPE_PUBLISHABLE_KEY_ENV, "").strip()


def get_premium_price_cents():
    raw_value = os.environ.get(STRIPE_PREMIUM_PRICE_CENTS_ENV, "").strip()
    try:
        return max(100, int(raw_value))
    except (TypeError, ValueError):
        return DEFAULT_PREMIUM_PRICE_CENTS


def stripe_enabled():
    return bool(get_stripe_secret_key() and get_stripe_publishable_key())


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
                cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT")
                cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT")
                cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS premium_status TEXT NOT NULL DEFAULT 'free'")
                cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS premium_plan TEXT")
                cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS premium_current_period_end TIMESTAMPTZ")
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
                           stripe_customer_id, stripe_subscription_id, premium_status,
                           premium_plan, premium_current_period_end, public_profile, public_alias
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
            "stripe_customer_id": row[5],
            "stripe_subscription_id": row[6],
            "premium_status": row[7] or "free",
            "premium_plan": row[8],
            "premium_current_period_end": row[9].isoformat() if row[9] else None,
            "public_profile": bool(row[10]) if row[10] is not None else False,
            "public_alias": row[11] or row[2]
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
                           stripe_customer_id, stripe_subscription_id, premium_status,
                           premium_plan, premium_current_period_end, public_profile, public_alias
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
            "stripe_customer_id": row[4],
            "stripe_subscription_id": row[5],
            "premium_status": row[6] or "free",
            "premium_plan": row[7],
            "premium_current_period_end": row[8].isoformat() if row[8] else None,
            "public_profile": bool(row[9]) if row[9] is not None else False,
            "public_alias": row[10] or row[2]
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
        "premium_status": user.get("premium_status", "free"),
        "premium_plan": user.get("premium_plan"),
        "premium_current_period_end": user.get("premium_current_period_end"),
        "is_premium": user.get("premium_status") == "active",
        "public_profile": bool(user.get("public_profile")),
        "public_alias": user.get("public_alias") or user["display_name"]
    }


def get_request_base_url():
    forwarded_proto = request.headers.get("X-Forwarded-Proto")
    scheme = forwarded_proto.split(",")[0].strip() if forwarded_proto else request.scheme
    host = request.headers.get("X-Forwarded-Host") or request.host
    return f"{scheme}://{host}"


def update_user_billing_fields(user_id, premium_status="free", premium_plan=None, premium_current_period_end=None,
                               stripe_customer_id=None, stripe_subscription_id=None):
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
                    SET premium_status = %s,
                        premium_plan = %s,
                        premium_current_period_end = %s,
                        stripe_customer_id = COALESCE(%s, stripe_customer_id),
                        stripe_subscription_id = %s
                    WHERE id = %s
                    """,
                    (
                        premium_status,
                        premium_plan,
                        premium_current_period_end,
                        stripe_customer_id,
                        stripe_subscription_id,
                        user_id
                    )
                )
        conn.close()
    except Exception as exc:
        print("Billing update failed:", exc)


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


def sync_user_subscription_from_stripe(user):
    if not user or not stripe_enabled():
        return user
    subscription_id = user.get("stripe_subscription_id")
    if not subscription_id:
        return user

    try:
        subscription = stripe.Subscription.retrieve(subscription_id)
        status = subscription.get("status", "incomplete")
        premium_status = "active" if status in {"active", "trialing", "past_due"} else "free"
        current_period_end = subscription.get("current_period_end")
        period_end_dt = datetime.fromtimestamp(current_period_end, tz=DEMO_TIMEZONE) if current_period_end else None
        update_user_billing_fields(
            user["id"],
            premium_status=premium_status,
            premium_plan="premium-monthly" if premium_status == "active" else None,
            premium_current_period_end=period_end_dt,
            stripe_customer_id=subscription.get("customer"),
            stripe_subscription_id=subscription_id if premium_status == "active" else None
        )
        return get_user_by_id(user["id"])
    except Exception as exc:
        print("Stripe sync failed:", exc)
        return user


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
            "premium_status": "free",
            "premium_plan": None,
            "premium_current_period_end": None
        }, None
    except Exception as exc:
        print("Create user failed:", exc)
        return None, "Could not create the account right now."


def authenticate_user(email, password):
    user = get_user_by_email(email)
    if not user or not check_password_hash(user["password_hash"], password or ""):
        return None
    synced = sync_user_subscription_from_stripe(user)
    return serialize_user(synced)


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
        "cached": preferred_source != "live",
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
        candle_rows = fetch_polygon_candles(symbol, tf) if get_polygon_api_key() else None
        source = "live"

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
            "source": source,
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
        "confidence": min(95, 45 + (dominant_score * 10) - (opposing_score * 5)),
        "setup_quality": (
            "High quality" if grade == "A" and action != "WAIT"
            else "Usable" if grade == "B" and action != "WAIT"
            else "Needs patience"
        )
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
    direction = "above" if trade_signal.get("action") == "BUY" else "below"
    level = levels["resistance"] if direction == "above" else levels["support"]
    return [
        {
            "label": f"{symbol} breaks {direction} {round(level, 2)}",
            "detail": f"That would align with the current {trade_signal.get('grade')} setup and {market_mode.get('label', 'current')} tape.",
            "priority": "High",
            "why_now": trade_signal.get("reason")
        },
        {
            "label": f"Momentum score pushes past {min(95, max(60, momentum_score['value'] + 8))}",
            "detail": "That would signal stronger continuation pressure instead of just a random price tick.",
            "priority": "Medium",
            "why_now": momentum_score.get("summary")
        },
        {
            "label": f"Setup weakens from {trade_signal.get('grade', 'C')}",
            "detail": "Useful as a protection alert if the current setup loses alignment.",
            "priority": "Risk",
            "why_now": market_mode.get("summary")
        }
    ]


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
    candle_result = fetch_and_cache_candles(symbol, "5m")
    using_demo = not (candle_result and candle_result["candles"])
    signal_candles = candle_result["candles"] if candle_result and candle_result["candles"] else build_demo_candles(symbol, "5m")
    session_candles = prepare_chart_candles(signal_candles, "5m") or signal_candles
    quote_data = build_quote_from_candles(session_candles)
    price = quote_data["price"]
    open_price = quote_data["open"]
    market_source = candle_result["source"] if candle_result else "demo"
    market_cached = candle_result["cached"] if candle_result else True
    market_stale = candle_result["stale"] if candle_result else False
    market_age = candle_result["age_seconds"] if candle_result else 0

    dollar_change = round(price - open_price, 2)
    change = round(((price - open_price) / open_price) * 100, 2)
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
    user = sync_user_subscription_from_stripe(get_current_user())
    return jsonify({
        "database_enabled": database_enabled,
        "stripe_enabled": stripe_enabled(),
        "premium_price_cents": get_premium_price_cents(),
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


def get_or_create_stripe_customer(user):
    if not user or not stripe_enabled():
        return None
    if user.get("stripe_customer_id"):
        return user["stripe_customer_id"]

    customer = stripe.Customer.create(
        email=user["email"],
        name=user["display_name"],
        metadata={"user_id": str(user["id"])}
    )
    update_user_billing_fields(user["id"], stripe_customer_id=customer["id"])
    return customer["id"]


@app.route("/billing/config")
def billing_config():
    user = sync_user_subscription_from_stripe(get_current_user())
    return jsonify({
        "stripe_enabled": stripe_enabled(),
        "publishable_key": get_stripe_publishable_key(),
        "premium_price_cents": get_premium_price_cents(),
        "currency": "usd",
        "user": serialize_user(user)
    })


@app.route("/billing/create-checkout-session", methods=["POST"])
def create_checkout_session():
    user = sync_user_subscription_from_stripe(get_current_user())
    if not user:
        return jsonify({"error": "Sign in first to upgrade to Premium."}), 401
    if not stripe_enabled():
        return jsonify({"error": "Billing is not configured yet."}), 400

    try:
        customer_id = get_or_create_stripe_customer(user)
        base_url = get_request_base_url()
        checkout_session = stripe.checkout.Session.create(
            mode="subscription",
            customer=customer_id,
            client_reference_id=str(user["id"]),
            success_url=f"{base_url}/?checkout=success&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{base_url}/?checkout=canceled",
            metadata={"user_id": str(user["id"]), "plan": "premium-monthly"},
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "product_data": {
                        "name": "Trading App Premium",
                        "description": "Premium subscription for advanced trading workspace features."
                    },
                    "unit_amount": get_premium_price_cents(),
                    "recurring": {"interval": "month"}
                },
                "quantity": 1
            }],
            allow_promotion_codes=True
        )
        return jsonify({"url": checkout_session.url})
    except Exception as exc:
        print("Stripe checkout failed:", exc)
        return jsonify({"error": "Could not start checkout right now."}), 500


@app.route("/billing/checkout-status")
def checkout_status():
    user = get_current_user()
    session_id = request.args.get("session_id", "").strip()
    if not user:
        return jsonify({"error": "Sign in first."}), 401
    if not stripe_enabled() or not session_id:
        return jsonify({"error": "Missing billing session."}), 400

    try:
        checkout_session = stripe.checkout.Session.retrieve(session_id)
        if str(checkout_session.get("client_reference_id")) != str(user["id"]):
            return jsonify({"error": "That checkout session does not belong to this user."}), 403

        subscription_id = checkout_session.get("subscription")
        customer_id = checkout_session.get("customer")
        payment_status = checkout_session.get("payment_status")
        if subscription_id and payment_status in {"paid", "no_payment_required"}:
            subscription = stripe.Subscription.retrieve(subscription_id)
            current_period_end = subscription.get("current_period_end")
            period_end_dt = datetime.fromtimestamp(current_period_end, tz=DEMO_TIMEZONE) if current_period_end else None
            update_user_billing_fields(
                user["id"],
                premium_status="active",
                premium_plan="premium-monthly",
                premium_current_period_end=period_end_dt,
                stripe_customer_id=customer_id,
                stripe_subscription_id=subscription_id
            )
            fresh_user = get_user_by_id(user["id"])
            return jsonify({"ok": True, "user": serialize_user(fresh_user)})

        return jsonify({"ok": False, "error": "Checkout has not completed yet."}), 400
    except Exception as exc:
        print("Stripe checkout status failed:", exc)
        return jsonify({"error": "Could not verify checkout right now."}), 500


@app.route("/billing/create-portal-session", methods=["POST"])
def create_portal_session():
    user = sync_user_subscription_from_stripe(get_current_user())
    if not user:
        return jsonify({"error": "Sign in first to manage billing."}), 401
    if not stripe_enabled():
        return jsonify({"error": "Billing is not configured yet."}), 400

    customer_id = get_or_create_stripe_customer(user)
    if not customer_id:
        return jsonify({"error": "Billing profile is unavailable right now."}), 400

    try:
        portal = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=f"{get_request_base_url()}/"
        )
        return jsonify({"url": portal.url})
    except Exception as exc:
        print("Stripe portal failed:", exc)
        return jsonify({"error": "Could not open billing management right now."}), 500


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
