// Burnmeter Electron preload — minimal, contextIsolation-safe, runs sandboxed.
// Marks the DOM so dashboard.css/js can adapt (drag-region topbar, overlay padding).
// No Node APIs are exposed to the page — the dashboard stays a plain web app. Only the
// shell's own local splash (file://) gets a bridge: its status line + the Retry button.
const { contextBridge, ipcRenderer } = require("electron");

window.addEventListener("DOMContentLoaded", () => {
  document.body.classList.add("electron");
  document.body.dataset.shell = "electron";
});

if (location.protocol === "file:") {
  contextBridge.exposeInMainWorld("burnmeterShell", {
    state: () => ipcRenderer.invoke("splash:state"),
    onState: (cb) => { ipcRenderer.on("splash:state", (_e, s) => cb(s)); },
    retry: () => ipcRenderer.send("splash:retry"),
  });
}
