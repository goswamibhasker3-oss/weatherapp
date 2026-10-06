"""PostgreSQL access layer (psycopg 3 + connection pool).

The database is optional: when DATABASE_URL is not set, every function here is a
no-op and the app keeps working with its in-memory cache. Database errors are
logged and swallowed so a DB outage can never break the weather lookup itself.
"""
import logging
import os
from datetime import datetime, timezone
from functools import wraps
from typing import LiteralString, cast

from psycopg.rows import dict_row
from psycopg.types.json import Json
from psycopg_pool import ConnectionPool

log = logging.getLogger("weather.db")
_pool = None


def enabled():
    return bool(os.getenv("DATABASE_URL", "").strip())


def get_pool():
    """Create the pool lazily, so each gunicorn worker gets its own after forking."""
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            os.environ["DATABASE_URL"],
            min_size=1,
            max_size=int(os.getenv("DB_POOL_MAX", "5")),
            timeout=5,
            kwargs={"row_factory": dict_row},
            open=False,
        )
        _pool.open()
    return _pool


def close_pool():
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def safe(default=None):
    """Run a DB function; on any failure log it and return `default`."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not enabled():
                return default
            try:
                return fn(*args, **kwargs)
            except Exception:  # noqa: BLE001 - DB problems must not break the API
                log.exception("database call failed: %s", fn.__name__)
                return default
        return wrapper
    return deco


def init_schema(path=None):
    """Create tables, indexes and views from database/schema.sql."""
    path = path or os.path.join(os.path.dirname(__file__), "database", "schema.sql")
    with open(path, encoding="utf-8") as f, get_pool().connection() as conn:
        conn.execute(cast(LiteralString, f.read()))


def status():
    """'disabled' (no DATABASE_URL), 'ok' or 'error'."""
    if not enabled():
        return "disabled"
    try:
        with get_pool().connection() as conn:
            conn.execute("SELECT 1")
        return "ok"
    except Exception:  # noqa: BLE001
        log.exception("database health check failed")
        return "error"


# ---------- writes ----------

def _upsert_city(conn, name, country, lat, lon):
    row = conn.execute(
        """INSERT INTO cities (name, country, lat, lon) VALUES (%s, %s, %s, %s)
           ON CONFLICT (name, country) DO UPDATE SET lat = EXCLUDED.lat, lon = EXCLUDED.lon
           RETURNING id""",
        (name, country or "", lat, lon),
    ).fetchone()
    return row["id"]


@safe()
def record_weather(shaped, query_text=None):
    """Save a snapshot of current weather; optionally log the user's search."""
    loc = shaped["location"]
    if shaped.get("observed_at") is None:
        return None
    observed = datetime.fromtimestamp(shaped["observed_at"], tz=timezone.utc)
    with get_pool().connection() as conn:  # one transaction
        city_id = _upsert_city(conn, loc["city"], loc.get("country"), loc.get("lat"), loc.get("lon"))
        conn.execute(
            """INSERT INTO weather_snapshots
                 (city_id, observed_at, units, temperature, feels_like, humidity, pressure,
                  visibility, wind_speed, wind_deg, condition, description, icon, raw)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (city_id, observed_at, units) DO NOTHING""",
            (city_id, observed, shaped["units"], shaped.get("temperature"), shaped.get("feels_like"),
             shaped.get("humidity"), shaped.get("pressure"), shaped.get("visibility"),
             shaped.get("wind_speed"), shaped.get("wind_deg"), shaped.get("condition"),
             shaped.get("description"), shaped.get("icon"), Json(shaped)),
        )
        if query_text:
            conn.execute("INSERT INTO search_history (city_id, query_text) VALUES (%s, %s)",
                         (city_id, query_text[:120]))
    return city_id


# ---------- shared cache ----------

@safe()
def cache_get(key):
    with get_pool().connection() as conn:
        row = conn.execute(
            "SELECT payload FROM api_cache WHERE cache_key = %s AND expires_at > now()", (key,)
        ).fetchone()
    return row["payload"] if row else None


@safe()
def cache_set(key, payload, ttl_seconds):
    with get_pool().connection() as conn:
        conn.execute("DELETE FROM api_cache WHERE expires_at < now()")
        conn.execute(
            """INSERT INTO api_cache (cache_key, payload, fetched_at, expires_at)
               VALUES (%s, %s, now(), now() + make_interval(secs => %s))
               ON CONFLICT (cache_key) DO UPDATE
               SET payload = EXCLUDED.payload, fetched_at = now(), expires_at = EXCLUDED.expires_at""",
            (key, Json(payload), ttl_seconds),
        )


# ---------- reads ----------

@safe(default=[])
def popular_cities(limit=6):
    with get_pool().connection() as conn:
        return conn.execute(
            "SELECT name, country, searches FROM popular_cities LIMIT %s", (limit,)
        ).fetchall()


@safe(default=[])
def snapshots(city, country="", limit=24):
    with get_pool().connection() as conn:
        rows = conn.execute(
            """SELECT s.observed_at, s.units, s.temperature, s.feels_like, s.humidity, s.pressure,
                      s.wind_speed, s.condition, s.description, s.icon
               FROM weather_snapshots s JOIN cities c ON c.id = s.city_id
               WHERE lower(c.name) = lower(%s) AND (%s = '' OR c.country = %s)
               ORDER BY s.observed_at DESC LIMIT %s""",
            (city, country, country, limit),
        ).fetchall()
    for r in rows:
        r["observed_at"] = r["observed_at"].isoformat()
    return rows