// Burnmeter desktop — Electron shell.
//
// Electron owns the window/Chromium; our existing Python backend runs as a
// SIDECAR (the same local web server the pip app uses). On launch we spawn the
// sidecar on 127.0.0.1:<port>, wait for /api/health, then load it in a window.
//
// DEV (this PoC): spawns the repo's `python -m burnmeter serve`.
// PROD (later):   spawns a bundled PyInstaller `burnmeter-server` binary from
//                 process.resourcesPath (electron-builder extraResources).
const { app, BrowserWindow, Menu, shell } = require("electron");
const { spawn } = require("child_process");
const http = require("http");
const path = require("path");

const HOST = "127.0.0.1";
const PORT = Number(process.env.BURNMETER_PORT || 7766); // PoC port (avoids 7654)
let pyProc = null;
let win = null;

function startSidecar() {
  const repo = path.resolve(__dirname, "..");
  const prodBin = path.join(
    process.resourcesPath || "",
    "server",
    process.platform === "win32" ? "burnmeter-server.exe" : "burnmeter-server"
  );
  const useProd = !require("fs").existsSync(path.join(repo, "burnmeter", "__init__.py"));
  const cmd = useProd
    ? prodBin
    : (process.platform === "win32" ? "python" : "python3");
  const args = useProd
    ? ["serve", "--host", HOST, "--port", String(PORT), "--no-browser", "--no-shortcut"]
    : ["-m", "burnmeter", "serve", "--host", HOST, "--port", String(PORT), "--no-browser", "--no-shortcut"];
  pyProc = spawn(cmd, args, {
    cwd: useProd ? path.dirname(prodBin) : repo,
    env: { ...process.env, PYTHONPATH: repo, PYTHONUTF8: "1" },
    stdio: "ignore",
    windowsHide: true,
  });
  pyProc.on("error", (e) => console.error("[sidecar] failed:", e.message));
}

function waitForServer(cb, tries = 80) {
  const probe = () => {
    http
      .get(`http://${HOST}:${PORT}/api/health`, (res) => { res.resume(); cb(); })
      .on("error", () => { if (--tries > 0) setTimeout(probe, 500); else cb(); });
  };
  probe();
}

function createWindow() {
  win = new BrowserWindow({
    width: 1480,
    height: 940,
    minWidth: 900,
    minHeight: 600,
    backgroundColor: "#0b0c0d",
    title: "Burnmeter",
    icon: path.join(__dirname, "..", "burnmeter", "assets", "burnmeter.png"),
    autoHideMenuBar: true,
    webPreferences: { contextIsolation: true },
  });
  Menu.setApplicationMenu(null);
  win.loadURL(`http://${HOST}:${PORT}/`);
  // open external links in the real browser, not inside the app window
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("http")) { shell.openExternal(url); return { action: "deny" }; }
    return { action: "allow" };
  });
}

app.whenReady().then(() => {
  startSidecar();
  waitForServer(createWindow);
});

app.on("window-all-closed", () => { if (process.platform !== "darwin") app.quit(); });
app.on("quit", () => { if (pyProc) { try { pyProc.kill(); } catch (e) {} } });
