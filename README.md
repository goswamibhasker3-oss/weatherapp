# Weather App

A responsive weather dashboard with live conditions, a five-day forecast, geolocation and search history. A small Flask backend proxies the OpenWeatherMap API so the secret key never reaches the browser.

**Author:** Bhaskar Goswami, B.Sc. Information Technology

## Features

- Search by city and optional country code (`Delhi` + `IN`, or `Delhi, IN` in one field)
- Current weather: temperature, feels-like, humidity, wind, condition and icon, pressure, visibility, sunrise and sunset
- Five-day forecast with high/low, condition, icon and rain probability
- "Use my location" via the browser Geolocation API (optional)
- Recent searches stored in `localStorage`, with a clear button
- °C / °F toggle and light/dark mode, both remembered
- Loading skeleton, and clear error states for: unknown city, API failure, network failure, missing API key, rate limits, denied location permission
- PostgreSQL storage (optional): searches, real weather snapshots for trends, popular cities, and a cache shared between workers
- Hero panel colours change with the weather (clear, night, cloud, rain, storm, snow, mist)

All data comes from the live API. Nothing is hard-coded.

## Tech stack

| Layer | Tools |
|---|---|
| Frontend | HTML, CSS, vanilla JavaScript (no framework, no build step) |
| Backend | Python 3.10+, Flask |
| Database | PostgreSQL 14+ via psycopg 3 and a connection pool |
| Weather data | OpenWeatherMap: Current Weather and 5 Day / 3 Hour Forecast (both free tier) |
| Tests | pytest (API is mocked) |

## Folder structure

```
weather-app/
├── app.py                 # Flask app: routes, validation, caching, error mapping
├── db.py                  # PostgreSQL access layer (optional, fails safe)
├── requirements.txt       # Runtime dependencies
├── requirements-dev.txt   # Adds pytest
├── .env.example           # Template for your secrets (copy to .env)
├── .gitignore
├── Procfile               # Production start command (Render, Railway, Heroku)
├── docker-compose.yml     # Local PostgreSQL in one command
├── pytest.ini
├── README.md
├── database/
│   ├── schema.sql         # All tables, indexes and the popular_cities view
│   └── scripts/
│       └── init_db.py     # Creates the schema from schema.sql
├── templates/
│   └── index.html
├── static/
│   ├── css/
│   │   └── style.css
│   └── js/
│       ├── app.js         # UI logic, fetch calls, localStorage
│       └── theme-init.js  # Applies saved theme before first paint
└── tests/
    ├── test_app.py        # API tests (no database needed)
    └── test_db.py         # PostgreSQL integration tests
```

## Get an API key

1. Create a free account at <https://openweathermap.org/api>.
2. Open **My API keys** in your account and copy the default key (or generate one).
3. A new key can take up to a couple of hours to activate. Until then the app shows "Weather service isn't set up" (the API returns 401).

## Database (PostgreSQL)

The database is optional. Without `DATABASE_URL` the app still works and uses an in-memory cache; with it you get stored history, trends, popular cities and a cache shared by all workers. If PostgreSQL goes down, weather lookups keep working and the error is logged.

### Tables

```
cities (1) ───< search_history        what visitors typed, counted per city
   │
   └──────────< weather_snapshots     real API observations kept for trends

api_cache                             shared cache of upstream responses (own key, no FK)
popular_cities (view)                 searches per city, most searched first
```

| Table | Key columns | Notes |
|---|---|---|
| `cities` | `id`, `name`, `country`, `lat`, `lon` | `UNIQUE (name, country)`, country is `''` or two capital letters |
| `search_history` | `id`, `city_id` → cities, `query_text`, `searched_at` | Written only for searches typed in the UI. No IP, no cookie, no coordinates |
| `weather_snapshots` | `id`, `city_id` → cities, `observed_at`, `units`, `temperature`, `feels_like`, `humidity`, `pressure`, `visibility`, `wind_speed`, `wind_deg`, `condition`, `description`, `icon`, `raw` (JSONB) | `UNIQUE (city_id, observed_at, units)` so one observation is stored once |
| `api_cache` | `cache_key` (PK), `payload` (JSONB), `fetched_at`, `expires_at` | Expired rows are removed on write |

### Set up locally

Option A: Docker (schema is applied automatically)

```bash
docker compose up -d
```

Option B: an existing PostgreSQL server

```bash
createdb weatherdb
# put your connection string in .env as DATABASE_URL=postgresql://user:password@localhost:5432/weatherdb
python database/scripts/init_db.py
```

The script is safe to run again; it never drops data.

## Run locally

```bash
git clone <your-repo-url> weather-app
cd weather-app

python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env              # Windows: copy .env.example .env
# edit .env and set OPENWEATHER_API_KEY=...

python app.py
```

Open <http://localhost:5000>.

Run the tests:

```bash
pip install -r requirements-dev.txt
pytest

# include the PostgreSQL tests (use a throw-away database, the tables are wiped)
TEST_DATABASE_URL=postgresql://weather:weather@localhost:5432/weatherdb pytest
```

### Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `OPENWEATHER_API_KEY` | yes | none | OpenWeatherMap key, read only on the server |
| `DATABASE_URL` | no | empty | PostgreSQL connection string. Empty disables the database features |
| `DB_POOL_MAX` | no | `5` | Max connections per worker |
| `PORT` | no | `5000` | Local dev port |
| `FLASK_DEBUG` | no | `0` | Set `1` for auto-reload in development only |
| `CACHE_TTL_SECONDS` | no | `600` | How long identical lookups are cached |
| `RATE_LIMIT_PER_MINUTE` | no | `60` | Per-IP limit on `/api/*` (`0` disables) |

## API reference

All endpoints return JSON. Errors always look like `{"error": {"code": "...", "message": "..."}}`.

| Endpoint | Description |
|---|---|
| `GET /api/weather?city=Delhi` | Current weather |
| `GET /api/weather?city=Delhi&country=IN` | Same, narrowed by country |
| `GET /api/weather?lat=28.61&lon=77.21` | Current weather by coordinates |
| `GET /api/forecast?city=Delhi` | Daily forecast (up to 5 days) |
| `GET /api/popular?limit=5` | Most searched cities (needs the database) |
| `GET /api/snapshots?city=Delhi&country=IN&limit=24` | Stored observations for a city, newest first (needs the database) |
| `GET /api/health` | Liveness, database status (`ok`, `error` or `disabled`) and whether a key is configured (never returns the key) |

Optional on both weather endpoints: `units=metric` (default) or `units=imperial`.

Example:

```bash
curl "http://localhost:5000/api/weather?city=Delhi&country=IN"
```

### Error codes

| Code | HTTP | Meaning |
|---|---|---|
| `invalid_request` | 400 | Missing or malformed parameters |
| `city_not_found` | 404 | No match for that place |
| `rate_limited` | 429 | Local or OpenWeatherMap rate limit hit |
| `missing_api_key` | 503 | `OPENWEATHER_API_KEY` is not set |
| `invalid_api_key` | 503 | Key rejected or not yet activated |
| `upstream_unreachable` | 502 | Server couldn't reach OpenWeatherMap |
| `upstream_timeout` | 504 | OpenWeatherMap took longer than 8 seconds |
| `upstream_error` | 502 | Any other upstream failure |
| `database_disabled` | 503 | A database-only endpoint was called without `DATABASE_URL` |

Network failures between the browser and your server are handled in `app.js` and shown as "No connection".

## Deployment

The app is a standard WSGI application. In production run it with `gunicorn` (already in `requirements.txt`), not `python app.py`.

### Render (simple, free tier available)

1. Push the project to GitHub (make sure `.env` is **not** committed).
2. On <https://render.com> create a PostgreSQL database (**New → PostgreSQL**) and copy its **Internal Database URL**.
3. Choose **New → Web Service** and connect the repo. Runtime: Python. Build command: `pip install -r requirements.txt`. Start command: `gunicorn app:app`.
4. Under **Environment**, add `OPENWEATHER_API_KEY` and `DATABASE_URL` (the URL from step 2).
5. Deploy, then open the service's **Shell** and run `python database/scripts/init_db.py` once to create the tables.
6. Render serves the site over HTTPS, which the Geolocation API requires outside `localhost`.

### Railway or Heroku

Both read the included `Procfile`. Set `OPENWEATHER_API_KEY` in the platform's variables or config vars and deploy from GitHub.

### Your own server (VPS)

Run `gunicorn app:app --bind 127.0.0.1:8000` under systemd and put Nginx in front as a reverse proxy with a Let's Encrypt certificate. Keep the key in the service's environment file, not in the repo.

### Production notes

- With PostgreSQL enabled the response cache is shared by all workers. The rate limiter is still per-process, so for several servers switch it to Flask-Limiter with Redis.
- Managed databases usually need SSL. If the connection fails from outside the provider's network, append `?sslmode=require` to `DATABASE_URL`.
- Behind a proxy, `request.remote_addr` is the proxy's address. Wrap the app with `werkzeug.middleware.proxy_fix.ProxyFix` so rate limiting sees real client IPs.

## Security notes

- The API key lives only in server environment variables. The browser calls `/api/*` on your own server, which calls OpenWeatherMap.
- `.env` is git-ignored. Only `.env.example` is committed.
- Inputs are validated (length, allowed characters, coordinate ranges, country format) before anything is sent upstream.
- API data is rendered with `textContent` and DOM APIs, never `innerHTML`, so responses can't inject markup.
- A strict Content-Security-Policy (no inline scripts), `X-Content-Type-Options`, `X-Frame-Options` and `Referrer-Policy` headers are set on every response.
- All SQL uses parameterised queries (no string-built SQL). The database stores no IP addresses, cookies or user accounts.
- Geolocation coordinates are rounded and sent only to your own server. They are never written to search history.

## Possible next steps

- Hourly forecast chart
- City autocomplete using OpenWeatherMap's Geocoding API
- Air quality and UV index
- PWA support for offline access to the last result