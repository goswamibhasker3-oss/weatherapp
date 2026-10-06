"""Backend tests. The OpenWeatherMap API is mocked; no network or real key needed."""
import pytest
import requests

import app as weather_app


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status_code, self._payload = status, payload or {}

    def json(self):
        return self._payload


CURRENT = {
    "name": "Delhi", "sys": {"country": "IN", "sunrise": 1, "sunset": 2}, "coord": {"lat": 28.6, "lon": 77.2},
    "timezone": 19800, "dt": 1700000000, "visibility": 4000,
    "main": {"temp": 24.3, "feels_like": 24.0, "humidity": 40, "pressure": 1012},
    "wind": {"speed": 3.1, "deg": 90},
    "weather": [{"main": "Haze", "description": "haze", "icon": "50d"}],
}
# Three-hourly slots across two local days (timezone +5:30).
FORECAST = {
    "city": {"name": "Delhi", "country": "IN", "timezone": 19800},
    "list": [
        {"dt": 1700000000 + i * 10800, "pop": p, "main": {"temp_min": t - 1, "temp_max": t + 1},
         "weather": [{"main": "Clouds", "description": "scattered clouds", "icon": "03n" if i % 2 else "03d"}]}
        for i, (t, p) in enumerate([(20, 0.1), (22, 0.6), (25, 0.2), (18, 0.0), (21, 0.3), (23, 0.9), (19, 0.0), (17, 0.1)])
    ],
}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    weather_app._cache.clear()
    weather_app._hits.clear()
    monkeypatch.setenv("OPENWEATHER_API_KEY", "test-key")


@pytest.fixture
def client():
    return weather_app.app.test_client()


def mock_upstream(monkeypatch, status=200, payload=None, exc=None):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, params))
        if exc:
            raise exc
        return FakeResponse(status, payload)

    monkeypatch.setattr(weather_app.requests, "get", fake_get)
    return calls


def test_homepage_static_assets_are_served(client):
    assert client.get("/").status_code == 200
    assert client.get("/static/css/style.css").mimetype == "text/css"
    assert client.get("/static/js/theme-init.js").mimetype == "text/javascript"
    assert client.get("/static/js/app.js").mimetype == "text/javascript"


def test_current_weather_is_shaped_and_key_stays_server_side(client, monkeypatch):
    calls = mock_upstream(monkeypatch, payload=CURRENT)
    r = client.get("/api/weather?city=Delhi&country=in")
    body = r.get_json()
    assert r.status_code == 200
    assert body["location"]["city"] == "Delhi" and body["temperature"] == 24.3
    assert calls[0][1]["q"] == "Delhi,IN"
    assert "test-key" not in r.get_data(as_text=True)


def test_forecast_groups_by_local_day_with_rain_probability(client, monkeypatch):
    mock_upstream(monkeypatch, payload=FORECAST)
    days = client.get("/api/forecast?city=Delhi").get_json()["days"]
    assert 1 <= len(days) <= 5
    assert all(d["icon"].endswith("d") for d in days)
    assert max(d["rain_probability"] for d in days) == 90


def test_missing_params_and_bad_input(client):
    assert client.get("/api/weather").status_code == 400
    assert client.get("/api/weather?city=<script>").status_code == 400
    assert client.get("/api/weather?city=Delhi&country=India").status_code == 400
    assert client.get("/api/weather?lat=999&lon=0").status_code == 400
    assert client.get("/api/weather?lat=28.6").status_code == 400
    assert client.get("/api/weather?lon=77.2").status_code == 400


def test_missing_api_key(client, monkeypatch):
    monkeypatch.setenv("OPENWEATHER_API_KEY", "")
    r = client.get("/api/weather?city=Delhi")
    assert r.status_code == 503 and r.get_json()["error"]["code"] == "missing_api_key"


@pytest.mark.parametrize("status,code,http", [
    (404, "city_not_found", 404), (401, "invalid_api_key", 503),
    (429, "rate_limited", 429), (500, "upstream_error", 502),
])
def test_upstream_errors_are_mapped(client, monkeypatch, status, code, http):
    mock_upstream(monkeypatch, status=status)
    r = client.get("/api/weather?city=Nowhere")
    assert r.status_code == http and r.get_json()["error"]["code"] == code


def test_network_failure_and_timeout(client, monkeypatch):
    mock_upstream(monkeypatch, exc=requests.ConnectionError())
    assert client.get("/api/weather?city=Delhi").get_json()["error"]["code"] == "upstream_unreachable"
    mock_upstream(monkeypatch, exc=requests.Timeout())
    weather_app._cache.clear()
    assert client.get("/api/weather?city=Delhi").status_code == 504


def test_results_are_cached(client, monkeypatch):
    calls = mock_upstream(monkeypatch, payload=CURRENT)
    client.get("/api/weather?city=Delhi")
    client.get("/api/weather?city=delhi")
    assert len(calls) == 1


def test_local_rate_limit(client, monkeypatch):
    monkeypatch.setattr(weather_app, "RATE_LIMIT", 2)
    mock_upstream(monkeypatch, payload=CURRENT)
    codes = [client.get("/api/weather?city=Delhi").status_code for _ in range(3)]
    assert codes == [200, 200, 429]