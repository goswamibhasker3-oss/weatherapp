"""Weather App backend (Flask).

Proxies OpenWeatherMap so the secret API key never reaches the browser,
validates input, normalises responses, caches results briefly and maps
upstream failures to clear, consistent JSON errors.
"""
import os
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

load_dotenv()

import db  # noqa: E402  (after load_dotenv so DATABASE_URL is available)

OWM_BASE = "https://api.openweathermap.org/data/2.5"
UPSTREAM_TIMEOUT = 8  # seconds
CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", "600"))
RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))
FORECAST_DAYS = 5

app = Flask(__name__)
_cache = {}
_hits = defaultdict(list)


class ApiError(Exception):
    def __init__(self, code, message, status):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


@app.errorhandler(ApiError)
def handle_api_error(err):
    return jsonify(error={"code": err.code, "message": err.message}), err.status


@app.errorhandler(404)
def handle_404(_):
    if request.path.startswith("/api/"):
        return jsonify(error={"code": "not_found", "message": "Unknown API endpoint."}), 404
    return render_template("index.html"), 404


@app.errorhandler(500)
def handle_500(_):
    return jsonify(error={"code": "server_error", "message": "Something went wrong on the server."}), 500


@app.before_request
def rate_limit():
    """Tiny per-IP sliding window so one visitor can't burn the API quota.
    Use Flask-Limiter + Redis for multi-process production deployments."""
    if not request.path.startswith("/api/") or RATE_LIMIT <= 0:
        return
    now = time.time()
    hits = [t for t in _hits[request.remote_addr] if now - t < 60]
    if len(hits) >= RATE_LIMIT:
        _hits[request.remote_addr] = hits
        raise ApiError("rate_limited", "Too many requests. Please wait a minute and try again.", 429)
    hits.append(now)
    _hits[request.remote_addr] = hits


@app.after_request
def security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Permissions-Policy"] = "geolocation=(self)"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; "
        "style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
        "img-src 'self' https://openweathermap.org data:; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------- input handling ----------

CITY_RE = re.compile(r"^[\w\s.,'’\-]+$", re.UNICODE)


def parse_query():
    """Return (owm_params, units) from the request, or raise ApiError."""
    units = request.args.get("units", "metric")
    if units not in ("metric", "imperial"):
        raise ApiError("invalid_request", "units must be 'metric' or 'imperial'.", 400)

    lat, lon = request.args.get("lat"), request.args.get("lon")
    if lat is not None or lon is not None:
        if lat is None or lon is None:
            raise ApiError("invalid_request", "lat and lon must both be numbers.", 400)
        try:
            lat_f, lon_f = float(lat), float(lon)
        except ValueError:
            raise ApiError("invalid_request", "lat and lon must both be numbers.", 400)
        if not (-90 <= lat_f <= 90 and -180 <= lon_f <= 180):
            raise ApiError("invalid_request", "Coordinates are out of range.", 400)
        return {"lat": round(lat_f, 4), "lon": round(lon_f, 4)}, units

    city = (request.args.get("city") or "").strip()
    country = (request.args.get("country") or "").strip().upper()
    if "," in city and not country:  # allow "Delhi, IN" in one field
        city, _, tail = city.rpartition(",")
        city, country = city.strip(), tail.strip().upper()
    if not city:
        raise ApiError("invalid_request", "Enter a city name, or pass lat and lon.", 400)
    if len(city) > 100 or not CITY_RE.match(city):
        raise ApiError("invalid_request", "That city name contains characters we can't search for.", 400)
    if country and not re.fullmatch(r"[A-Z]{2}", country):
        raise ApiError("invalid_request", "Country must be a two-letter code such as IN, US or GB.", 400)
    return {"q": f"{city},{country}" if country else city}, units


# ---------- upstream ----------

def owm_get(endpoint, params, units):
    api_key = os.getenv("OPENWEATHER_API_KEY", "").strip()
    if not api_key or api_key == "your_api_key_here":
        raise ApiError("missing_api_key",
                       "The server has no OpenWeatherMap API key. Set OPENWEATHER_API_KEY in .env.", 503)

    full = {**params, "units": units}
    cache_key = (endpoint, tuple(sorted((k, str(v).lower()) for k, v in full.items())))
    hit = _cache.get(cache_key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    db_key = f"{endpoint}:{cache_key[1]}"
    stored = db.cache_get(db_key)  # shared across workers; None if the DB is off
    if stored is not None:
        _cache[cache_key] = (time.time(), stored)
        return stored

    try:
        r = requests.get(f"{OWM_BASE}/{endpoint}", params={**full, "appid": api_key},
                         timeout=UPSTREAM_TIMEOUT)
    except requests.Timeout:
        raise ApiError("upstream_timeout", "The weather service took too long to respond.", 504)
    except requests.RequestException:
        raise ApiError("upstream_unreachable", "Couldn't reach the weather service.", 502)

    if r.status_code == 404:
        raise ApiError("city_not_found", "We couldn't find that place. Check the spelling or add a country code.", 404)
    if r.status_code == 401:
        raise ApiError("invalid_api_key",
                       "The weather API key was rejected. New keys can take a couple of hours to activate.", 503)
    if r.status_code == 429:
        raise ApiError("rate_limited", "The weather service rate limit was reached. Try again shortly.", 429)
    if r.status_code == 400:
        raise ApiError("invalid_request", "The weather service couldn't understand that search.", 400)
    if r.status_code != 200:
        raise ApiError("upstream_error", f"The weather service returned an error ({r.status_code}).", 502)

    try:
        data = r.json()
    except ValueError:
        raise ApiError("upstream_error", "The weather service sent an unreadable response.", 502)

    if len(_cache) > 500:
        _cache.clear()
    _cache[cache_key] = (time.time(), data)
    db.cache_set(db_key, data, CACHE_TTL)
    return data


# ---------- shaping ----------

def shape_current(d, units):
    w = (d.get("weather") or [{}])[0]
    main, wind = d.get("main", {}), d.get("wind", {})
    return {
        "location": {"city": d.get("name") or "Selected location",
                     "country": d.get("sys", {}).get("country"),
                     "lat": d.get("coord", {}).get("lat"), "lon": d.get("coord", {}).get("lon")},
        "units": units,
        "timezone_offset": d.get("timezone", 0),
        "observed_at": d.get("dt"),
        "sunrise": d.get("sys", {}).get("sunrise"),
        "sunset": d.get("sys", {}).get("sunset"),
        "temperature": main.get("temp"),
        "feels_like": main.get("feels_like"),
        "humidity": main.get("humidity"),
        "pressure": main.get("pressure"),
        "visibility": d.get("visibility"),          # metres
        "wind_speed": wind.get("speed"),             # m/s (metric) or mph (imperial)
        "wind_deg": wind.get("deg"),
        "condition": w.get("main"),
        "description": w.get("description"),
        "icon": w.get("icon"),
    }


def shape_forecast(d, units):
    """Collapse OpenWeatherMap's 3-hourly list into one entry per local day."""
    tz = d.get("city", {}).get("timezone", 0)
    days = defaultdict(list)
    for item in d.get("list", []):
        local = datetime.fromtimestamp(item["dt"] + tz, tz=timezone.utc)
        days[local.date()].append((local, item))

    out = []
    for date in sorted(days)[:FORECAST_DAYS]:
        entries = days[date]
        # Representative slot = the one closest to midday; icon forced to day variant.
        _, rep = min(entries, key=lambda e: abs(e[0].hour - 12))
        w = (rep.get("weather") or [{}])[0]
        pops = [i["pop"] for _, i in entries if isinstance(i.get("pop"), (int, float))]
        common = Counter((i.get("weather") or [{}])[0].get("main") for _, i in entries).most_common(1)
        out.append({
            "date": date.isoformat(),
            "temp_min": min(i["main"]["temp_min"] for _, i in entries),
            "temp_max": max(i["main"]["temp_max"] for _, i in entries),
            "condition": common[0][0] if common else w.get("main"),
            "description": w.get("description"),
            "icon": (w.get("icon") or "01d")[:2] + "d",
            "rain_probability": round(max(pops) * 100) if pops else None,
        })
    return {"location": {"city": d.get("city", {}).get("name"), "country": d.get("city", {}).get("country")},
            "units": units, "days": out}


# ---------- routes ----------

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def health():
    return jsonify(status="ok", database=db.status(),
                   api_key_configured=bool(os.getenv("OPENWEATHER_API_KEY", "").strip()))


@app.get("/api/weather")
def weather():
    params, units = parse_query()
    shaped = shape_current(owm_get("weather", params, units), units)
    # Log the search only when the UI says the user typed it (not on reloads or unit toggles).
    track = request.args.get("track") == "1" and "q" in params
    db.record_weather(shaped, query_text=params["q"] if track else None)
    return jsonify(shaped)


@app.get("/api/forecast")
def forecast():
    params, units = parse_query()
    return jsonify(shape_forecast(owm_get("forecast", params, units), units))


@app.get("/api/popular")
def popular():
    if not db.enabled():
        raise ApiError("database_disabled", "Set DATABASE_URL to enable this feature.", 503)
    limit = max(1, min(request.args.get("limit", default=6, type=int), 20))
    rows = db.popular_cities(limit)
    return jsonify(cities=[{"city": r["name"], "country": r["country"], "searches": r["searches"]} for r in rows])


@app.get("/api/snapshots")
def snapshots():
    """Stored real observations for a city, newest first (for trends)."""
    if not db.enabled():
        raise ApiError("database_disabled", "Set DATABASE_URL to enable this feature.", 503)
    city = (request.args.get("city") or "").strip()
    country = (request.args.get("country") or "").strip().upper()
    if not city or len(city) > 100 or not CITY_RE.match(city):
        raise ApiError("invalid_request", "Provide a valid city.", 400)
    if country and not re.fullmatch(r"[A-Z]{2}", country):
        raise ApiError("invalid_request", "Country must be a two-letter code.", 400)
    limit = max(1, min(request.args.get("limit", default=24, type=int), 200))
    return jsonify(city=city, country=country, snapshots=db.snapshots(city, country, limit))


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG") == "1", port=int(os.getenv("PORT", "5000")))