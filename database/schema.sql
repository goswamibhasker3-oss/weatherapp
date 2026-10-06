-- Weather App: PostgreSQL schema (safe to run more than once)
-- Run:  python scripts/init_db.py   or   psql "$DATABASE_URL" -f database/schema.sql

-- 1. cities: one row per place we have looked up
CREATE TABLE IF NOT EXISTS cities (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT        NOT NULL,
    country     TEXT        NOT NULL DEFAULT '',
    lat         NUMERIC(8,5),
    lon         NUMERIC(8,5),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT cities_country_chk CHECK (country = '' OR country ~ '^[A-Z]{2}$'),
    CONSTRAINT cities_lat_chk     CHECK (lat IS NULL OR lat BETWEEN -90 AND 90),
    CONSTRAINT cities_lon_chk     CHECK (lon IS NULL OR lon BETWEEN -180 AND 180),
    CONSTRAINT cities_name_country_uq UNIQUE (name, country)
);

-- 2. search_history: what visitors typed into the search box (no personal data stored)
CREATE TABLE IF NOT EXISTS search_history (
    id          BIGSERIAL PRIMARY KEY,
    city_id     BIGINT      NOT NULL REFERENCES cities(id) ON DELETE CASCADE,
    query_text  TEXT        NOT NULL,
    searched_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_search_history_city ON search_history (city_id);
CREATE INDEX IF NOT EXISTS idx_search_history_time ON search_history (searched_at DESC);

-- 3. weather_snapshots: real observations returned by the API, kept for trends
CREATE TABLE IF NOT EXISTS weather_snapshots (
    id           BIGSERIAL PRIMARY KEY,
    city_id      BIGINT      NOT NULL REFERENCES cities(id) ON DELETE CASCADE,
    observed_at  TIMESTAMPTZ NOT NULL,
    units        TEXT        NOT NULL CHECK (units IN ('metric', 'imperial')),
    temperature  REAL,
    feels_like   REAL,
    humidity     SMALLINT CHECK (humidity IS NULL OR humidity BETWEEN 0 AND 100),
    pressure     INTEGER,
    visibility   INTEGER,          -- metres
    wind_speed   REAL,             -- m/s (metric) or mph (imperial)
    wind_deg     SMALLINT,
    condition    TEXT,
    description  TEXT,
    icon         TEXT,
    raw          JSONB       NOT NULL,
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT snapshots_unique UNIQUE (city_id, observed_at, units)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_city_time ON weather_snapshots (city_id, observed_at DESC);

-- 4. api_cache: shared cache of upstream responses (works across gunicorn workers)
CREATE TABLE IF NOT EXISTS api_cache (
    cache_key   TEXT PRIMARY KEY,
    payload     JSONB       NOT NULL,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_api_cache_expires ON api_cache (expires_at);

-- View: most searched cities
CREATE OR REPLACE VIEW popular_cities AS
SELECT c.id, c.name, c.country,
       COUNT(*)             AS searches,
       MAX(h.searched_at)   AS last_searched
FROM search_history h
JOIN cities c ON c.id = h.city_id
GROUP BY c.id, c.name, c.country
ORDER BY searches DESC, last_searched DESC;