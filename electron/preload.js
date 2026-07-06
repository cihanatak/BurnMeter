// Burnmeter Electron preload — minimal, contextIsolation-safe.
// Marks the DOM so dashboard.css/js can adapt (drag-region topbar, overlay padding).
// No Node APIs are exposed to the page — the dashboard stays a plain web app.
window.addEventListener("DOMContentLoaded", () => {
  document.body.classList.add("electron");
  document.body.dataset.shell = "electron";
});
