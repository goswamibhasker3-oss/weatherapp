// Runs before first paint to avoid a light/dark flash.
(function () {
  var t = null;
  try { t = JSON.parse(localStorage.getItem("weather.theme")); } catch (e) {}
  if (t !== "light" && t !== "dark") {
    t = window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  document.documentElement.dataset.theme = t;
})();