(() => {
  "use strict";

  const $ = (s) => document.querySelector(s);
  const KEYS = { history: "weather.history", units: "weather.units", theme: "weather.theme" };
  const MAX_HISTORY = 6;

  const store = {
    get(key, fallback) {
      try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); }
      catch { return fallback; }
    },
    set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* storage blocked */ } },
  };

  let units = store.get(KEYS.units, "metric") === "imperial" ? "imperial" : "metric";
  let lastQuery = null;
  let requestId = 0;

  const el = {
    form: $("#search-form"), city: $("#city"), country: $("#country"), locate: $("#locate"),
    history: $("#history"), historyList: $("#history-list"), clearHistory: $("#clear-history"),
    popular: $("#popular"), popularList: $("#popular-list"),
    status: $("#status"), retry: $("#retry"), themeToggle: $("#theme-toggle"),
    views: { empty: $("#state-empty"), loading: $("#state-loading"), error: $("#state-error"), result: $("#result") },
  };

  class AppError extends Error {
    constructor(code, message) { super(message); this.code = code; }
  }

  const ERROR_TITLES = {
    city_not_found: "Place not found",
    invalid_request: "Check your search",
    missing_api_key: "Weather service isn't set up",
    invalid_api_key: "Weather service isn't set up",
    rate_limited: "Too many requests",
    upstream_timeout: "Weather service is slow",
    upstream_unreachable: "Weather service unavailable",
    upstream_error: "Weather service unavailable",
    network: "No connection",
    geo_denied: "Location access is blocked",
    geo_unavailable: "Location unavailable",
    geo_timeout: "Location timed out",
    geo_unsupported: "Location isn't supported",
  };

  // ---------- helpers ----------
  const setView = (name) => {
    for (const [k, node] of Object.entries(el.views)) node.hidden = k !== name;
  };
  const announce = (msg) => { el.status.textContent = msg; };
  const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);
  const round = (v) => (num(v) === null ? "–" : String(Math.round(v)));
  const deg = () => (units === "imperial" ? "°F" : "°C");

  function make(tag, className, text) {
    const n = document.createElement(tag);
    if (className) n.className = className;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  function iconUrl(code) {
    return /^\d{2}[dn]$/.test(code || "") ? `https://openweathermap.org/img/wn/${code}@2x.png` : "";
  }

  function skyFor(icon, condition) {
    const code = (icon || "").slice(0, 2);
    const night = (icon || "").endsWith("n");
    if (code === "01") return night ? "clear-night" : "clear-day";
    if (code === "02" || code === "03" || code === "04") return night ? "clear-night" : "clouds";
    if (code === "09" || code === "10") return "rain";
    if (code === "11") return "storm";
    if (code === "13") return "snow";
    if (code === "50") return "mist";
    return /rain|drizzle/i.test(condition || "") ? "rain" : "clear-day";
  }

  const windText = (ms) => {
    if (num(ms) === null) return "–";
    return units === "imperial" ? `${Math.round(ms)} mph` : `${Math.round(ms * 3.6)} km/h`;
  };
  const compass = (d) => num(d) === null ? "" : ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][Math.round(d / 45) % 8];
  const visibilityText = (m) => {
    if (num(m) === null) return "–";
    return units === "imperial" ? `${(m / 1609.34).toFixed(1)} mi` : `${(m / 1000).toFixed(1)} km`;
  };
  const clock = (ts, offset) => {
    if (num(ts) === null) return "–";
    return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit", timeZone: "UTC" })
      .format(new Date((ts + (offset || 0)) * 1000));
  };

  // ---------- API ----------
  async function api(path, params) {
    let res;
    try {
      res = await fetch(`${path}?${params}`, { headers: { Accept: "application/json" } });
    } catch {
      throw new AppError("network", "We couldn't reach the server. Check your internet connection and try again.");
    }
    let body = null;
    try { body = await res.json(); } catch { /* non-JSON error page */ }
    if (!res.ok) {
      const e = body && body.error;
      throw new AppError((e && e.code) || "upstream_error", (e && e.message) || `The request failed (${res.status}).`);
    }
    return body;
  }

  async function loadWeather(query, { remember = false } = {}) {
    const id = ++requestId;
    lastQuery = query;
    setView("loading");
    announce("Loading weather");
    try {
      const params = new URLSearchParams({ ...query, units });
      if (remember) params.set("track", "1"); // lets the server count typed searches
      const [current, forecast] = await Promise.all([api("/api/weather", params), api("/api/forecast", params)]);
      if (id !== requestId) return;
      renderCurrent(current);
      renderForecast(forecast, current);
      setView("result");
      announce(`Showing weather for ${current.location.city}`);
      if (remember) {
        addHistory({ city: current.location.city, country: current.location.country || "" });
        loadPopular();
      }
    } catch (err) {
      if (id === requestId) showError(err);
    }
  }

  // ---------- rendering ----------
  function renderCurrent(d) {
    const loc = d.location;
    document.body.dataset.sky = skyFor(d.icon, d.condition);
    $("#place").textContent = loc.country ? `${loc.city}, ${loc.country}` : loc.city;
    $("#temp").textContent = round(d.temperature);
    $("#temp-unit").textContent = deg();
    const icon = $("#cond-icon");
    icon.src = iconUrl(d.icon);
    icon.hidden = !icon.src;
    $("#cond-text").textContent = d.description || d.condition || "";
    $("#feels").textContent = `Feels like ${round(d.feels_like)}${deg()}`;

    const wind = windText(d.wind_speed);
    const stats = [
      ["Humidity", num(d.humidity) === null ? "–" : `${d.humidity}%`],
      ["Wind", `${wind} ${compass(d.wind_deg)}`.trim()],
      ["Pressure", num(d.pressure) === null ? "–" : `${d.pressure} hPa`],
      ["Visibility", visibilityText(d.visibility)],
      ["Sunrise", clock(d.sunrise, d.timezone_offset)],
      ["Sunset", clock(d.sunset, d.timezone_offset)],
    ];
    const dl = $("#stats");
    dl.replaceChildren(...stats.map(([label, value]) => {
      const box = make("div", "stat");
      box.append(make("dt", "", label), make("dd", "", value));
      return box;
    }));
  }

  function renderForecast(f, current) {
    const localToday = new Date(((current.observed_at || 0) + (current.timezone_offset || 0)) * 1000)
      .toISOString().slice(0, 10);
    const weekday = new Intl.DateTimeFormat(undefined, { weekday: "short", timeZone: "UTC" });
    const dayMonth = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", timeZone: "UTC" });

    $("#forecast").replaceChildren(...f.days.map((day) => {
      const date = new Date(`${day.date}T12:00:00Z`);
      const card = make("li", "day");
      card.append(make("span", "day-name", day.date === localToday ? "Today" : weekday.format(date)));
      card.append(make("span", "day-date", dayMonth.format(date)));

      const img = make("img");
      img.width = img.height = 56;
      img.alt = day.description || day.condition || "";
      img.src = iconUrl(day.icon);
      card.append(img);

      const temps = make("span", "day-temps", `${round(day.temp_max)}${deg()} `);
      temps.append(make("span", "lo", `${round(day.temp_min)}${deg()}`));
      card.append(temps, make("span", "day-cond", day.description || day.condition || ""));

      const pop = num(day.rain_probability);
      const rain = make("div", "rain", pop === null ? "Rain chance unavailable" : `Rain chance ${pop}%`);
      if (pop !== null) {
        const bar = make("div", "rain-bar");
        const fill = make("span");
        fill.style.width = `${Math.min(100, Math.max(0, pop))}%`;
        bar.append(fill);
        rain.append(bar);
      }
      card.append(rain);
      return card;
    }));
  }

  function showError(err) {
    const code = err instanceof AppError ? err.code : "upstream_error";
    $("#error-title").textContent = ERROR_TITLES[code] || "Something went wrong";
    $("#error-message").textContent = err instanceof AppError ? err.message : "An unexpected error occurred. Please try again.";
    el.retry.hidden = !lastQuery || code === "geo_denied" || code === "geo_unsupported";
    setView("error");
    announce($("#error-title").textContent);
  }

  // ---------- search history ----------
  const readHistory = () => {
    const h = store.get(KEYS.history, []);
    return Array.isArray(h) ? h.filter((x) => x && typeof x.city === "string").slice(0, MAX_HISTORY) : [];
  };

  function addHistory(entry) {
    const same = (a, b) => a.city.toLowerCase() === b.city.toLowerCase() && (a.country || "") === (b.country || "");
    const next = [entry, ...readHistory().filter((h) => !same(h, entry))].slice(0, MAX_HISTORY);
    store.set(KEYS.history, next);
    renderHistory();
  }

  function renderHistory() {
    const items = readHistory();
    el.history.hidden = items.length === 0;
    el.historyList.replaceChildren(...items.map((h) => {
      const li = make("li");
      const b = make("button", "chip", h.country ? `${h.city}, ${h.country}` : h.city);
      b.type = "button";
      b.addEventListener("click", () => {
        el.city.value = h.city;
        el.country.value = h.country || "";
        search();
      });
      li.append(b);
      return li;
    }));
  }

  // ---------- popular searches (needs the PostgreSQL backend) ----------
  async function loadPopular() {
    try {
      const health = await fetch("/api/health", { headers: { Accept: "application/json" } });
      if (!health.ok || (await health.json()).database !== "ok") return;
      const res = await fetch("/api/popular?limit=5", { headers: { Accept: "application/json" } });
      if (!res.ok) return; // database not configured: just hide the row
      const { cities } = await res.json();
      el.popular.hidden = cities.length === 0;
      el.popularList.replaceChildren(...cities.map((c) => {
        const li = make("li");
        const b = make("button", "chip", c.country ? `${c.city}, ${c.country}` : c.city);
        b.type = "button";
        b.addEventListener("click", () => {
          el.city.value = c.city;
          el.country.value = c.country || "";
          search();
        });
        li.append(b);
        return li;
      }));
    } catch { /* optional feature */ }
  }

  // ---------- actions ----------
  function search() {
    const city = el.city.value.trim();
    const country = el.country.value.trim().toUpperCase();
    if (!city) { el.city.focus(); return showError(new AppError("invalid_request", "Enter a city name to search.")); }
    if (country && !/^[A-Z]{2}$/.test(country)) {
      return showError(new AppError("invalid_request", "Use a two-letter country code such as IN, US or GB, or leave it blank."));
    }
    loadWeather(country ? { city, country } : { city }, { remember: true });
  }

  function locate() {
    if (!("geolocation" in navigator)) {
      return showError(new AppError("geo_unsupported", "This browser can't share your location. Search for a city instead."));
    }
    announce("Finding your location");
    setView("loading");
    el.locate.disabled = true;
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        el.locate.disabled = false;
        loadWeather({ lat: pos.coords.latitude.toFixed(4), lon: pos.coords.longitude.toFixed(4) });
      },
      (err) => {
        el.locate.disabled = false;
        const map = {
          1: ["geo_denied", "Location permission was denied. Allow it in your browser's site settings, or search for a city instead."],
          2: ["geo_unavailable", "Your position couldn't be determined. Search for a city instead."],
          3: ["geo_timeout", "Finding your location took too long. Try again, or search for a city."],
        };
        const [code, msg] = map[err.code] || map[2];
        lastQuery = null;
        showError(new AppError(code, msg));
      },
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 }
    );
  }

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    el.themeToggle.setAttribute("aria-label", theme === "dark" ? "Switch to light mode" : "Switch to dark mode");
  }

  function syncUnitButtons() {
    document.querySelectorAll("[data-units]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.units === units)));
  }

  // ---------- wiring ----------
  el.form.addEventListener("submit", (e) => { e.preventDefault(); search(); });
  el.locate.addEventListener("click", locate);
  el.retry.addEventListener("click", () => lastQuery && loadWeather(lastQuery));
  el.clearHistory.addEventListener("click", () => { store.set(KEYS.history, []); renderHistory(); });
  el.themeToggle.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    store.set(KEYS.theme, next);
    applyTheme(next);
  });
  document.querySelectorAll("[data-units]").forEach((b) => b.addEventListener("click", () => {
    if (b.dataset.units === units) return;
    units = b.dataset.units;
    store.set(KEYS.units, units);
    syncUnitButtons();
    if (lastQuery) loadWeather(lastQuery);
  }));

  applyTheme(document.documentElement.dataset.theme || "light");
  syncUnitButtons();
  renderHistory();
  loadPopular();
  const recent = readHistory()[0];
  if (recent) {
    el.city.value = recent.city;
    el.country.value = recent.country || "";
    loadWeather(recent.country ? { city: recent.city, country: recent.country } : { city: recent.city });
  }
})();