"""PostgreSQL integration tests. Skipped unless TEST_DATABASE_URL is set, e.g.
   TEST_DATABASE_URL=postgresql://weather:weather@localhost:5432/weatherdb pytest
They create the schema and wipe the tables, so use a throw-away database."""
import os

import pytest
from psycopg import sql

import app as weather_app
import db
from test_app import CURRENT, FORECAST, mock_upstream  # reuse the mocked OpenWeatherMap data

URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture(autouse=True)
def database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("OPENWEATHER_API_KEY", "test-key")
    db.close_pool()
    db.init_schema()
    with db.get_pool().connection() as conn:
        conn.execute("TRUNCATE cities, api_cache RESTART IDENTITY CASCADE")
    weather_app._cache.clear()
    weather_app._hits.clear()
    yield
    db.close_pool()


@pytest.fixture
def client():
    return weather_app.app.test_client()


def count(table):
    with db.get_pool().connection() as conn:
        row = conn.execute(sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(table))).fetchone()
        assert row is not None
        return row["n"]


def test_schema_creates_all_tables():
    with db.get_pool().connection() as conn:
        names = {r["table_name"] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'").fetchall()}
    assert {"cities", "search_history", "weather_snapshots", "api_cache", "popular_cities"} <= names


def test_weather_is_stored_and_search_tracked_only_when_asked(client, monkeypatch):
    mock_upstream(monkeypatch, payload=CURRENT)
    client.get("/api/weather?city=Delhi&country=IN")            # reload / unit toggle: no search logged
    assert count("weather_snapshots") == 1 and count("search_history") == 0
    client.get("/api/weather?city=Delhi&country=IN&track=1")    # typed search: logged
    assert count("search_history") == 1
    assert count("weather_snapshots") == 1                       # same observation is not duplicated
    assert count("cities") == 1


def test_geolocation_lookups_are_never_logged_as_searches(client, monkeypatch):
    mock_upstream(monkeypatch, payload=CURRENT)
    client.get("/api/weather?lat=28.61&lon=77.21&track=1")
    assert count("search_history") == 0


def test_popular_and_snapshots_endpoints(client, monkeypatch):
    mock_upstream(monkeypatch, payload=CURRENT)
    for _ in range(2):
        client.get("/api/weather?city=Delhi&country=IN&track=1")
    popular = client.get("/api/popular").get_json()["cities"]
    assert popular[0]["city"] == "Delhi" and popular[0]["searches"] == 2
    snaps = client.get("/api/snapshots?city=delhi&country=IN").get_json()["snapshots"]
    assert len(snaps) == 1 and snaps[0]["temperature"] == pytest.approx(24.3)


def test_cache_is_shared_through_the_database(client, monkeypatch):
    calls = mock_upstream(monkeypatch, payload=FORECAST)
    client.get("/api/forecast?city=Delhi")
    weather_app._cache.clear()           # simulate another worker with an empty memory cache
    client.get("/api/forecast?city=Delhi")
    assert len(calls) == 1 and count("api_cache") == 1


def test_database_failure_does_not_break_weather(client, monkeypatch):
    mock_upstream(monkeypatch, payload=CURRENT)
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody:x@127.0.0.1:1/none")
    db.close_pool()
    monkeypatch.setattr(db, "get_pool", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    r = client.get("/api/weather?city=Delhi")
    assert r.status_code == 200 and r.get_json()["temperature"] == pytest.approx(24.3)


def test_health_reports_database(client):
    assert client.get("/api/health").get_json()["database"] == "ok"


def test_endpoints_explain_when_database_is_disabled(client, monkeypatch):
    monkeypatch.delenv("DATABASE_URL")
    r = client.get("/api/popular")
    assert r.status_code == 503 and r.get_json()["error"]["code"] == "database_disabled"