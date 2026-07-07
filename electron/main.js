// Burnmeter desktop — Electron shell (production).
//
// Electron owns the window/tray/Chromium; the existing Python backend runs as a
// SIDECAR (the same local web server the pip app uses). Launch order:
//   1. If a Burnmeter server is ALREADY healthy on the port (the user's tray from a
//      previous session, or the pywebview app) → ATTACH, don't spawn a second one.
//   2. Else spawn the sidecar (dev: repo python; prod: bundled PyInstaller
//      burnmeter-server from process.resourcesPath), wait for /api/health, load.
//
// Window model matches the product's: closing the window HIDES to tray (the meter
// keeps collecting); Quit lives in the tray menu. We only kill the sidecar on quit
// if WE spawned it — never someone else's server.
const { app, BrowserWindow, Menu, Tray, shell, nativeImage, nativeTheme, screen } = require("electron");
const { spawn } = require("child_process");
const http = require("http");
const fs = require("fs");
const path = require("path");

const HOST = "127.0.0.1";
const PORT = Number(process.env.BURNMETER_PORT || 7654);   // same port as the shipped app
const BASE = `http://${HOST}:${PORT}`;
const STATE_FILE = () => path.join(app.getPath("userData"), "window-state.json");

// ---------- assets: dev (repo) vs packaged (resourcesPath) ----------
// In the packaged app __dirname lives inside app.asar; the repo layout doesn't exist.
// extraResources puts the icons at resources/burnmeter.png + resources/burnmeter.ico.
function assetPath(name) {
  const devP = path.join(__dirname, "..", "burnmeter", "assets", name);
  if (fs.existsSync(devP)) return devP;
  const prodP = path.join(process.resourcesPath || "", name);
  return fs.existsSync(prodP) ? prodP : null;
}
function loadIcon(preferIco) {
  const names = preferIco && process.platform === "win32"
    ? ["burnmeter.ico", "burnmeter.png"] : ["burnmeter.png", "burnmeter.ico"];
  for (const n of names) {
    const p = assetPath(n);
    if (p) {
      const img = nativeImage.createFromPath(p);
      if (!img.isEmpty()) return img;
    }
  }
  return nativeImage.createEmpty();
}

// The shell GUARANTEES its own chrome: the drag region + dark color-scheme are injected
// here, so the window behaves no matter which server version it attaches to (an older
// installed server serves CSS without the body.electron rules — that made the window
// unmovable once; a page without color-scheme:dark gets LIGHT Chromium scrollbars).
const DRAG_CSS = `
  html { color-scheme: dark; }
  .topbar {
    position: sticky; top: 0; z-index: 80;
    background: var(--bg-base, #0F1011);
    box-shadow: 0 1px 0 var(--border-subtle, rgba(255,255,255,.06));
    -webkit-app-region: drag;
    /* Exact window-controls width via Chromium's WCO env vars — DPI/scale-proof.
       (A fixed px pad overlapped the refresh button on scaled displays.) */
    padding-right: calc(100vw - env(titlebar-area-width, calc(100vw - 170px)));
  }
  .topbar button, .topbar a, .topbar select, .topbar input,
  .topbar .picker, .topbar .zoom-ctl, .topbar .icon-btn { -webkit-app-region: no-drag; }
  .sidebar .side-brand { -webkit-app-region: drag; }
  .sidebar .side-brand * { -webkit-app-region: no-drag; }
`;
const OVERLAY = { color: "#0F1011", symbolColor: "#9BA1A6", height: 42 };  // = --bg-base (seamless bar)

let pyProc = null;        // sidecar process IF we spawned it (else null = attached)
let win = null;
let tray = null;
let quitting = false;

// ---------- single instance: a real app has ONE window ----------
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (win) { if (win.isMinimized()) win.restore(); win.show(); win.focus(); }
  });
}

// ---------- sidecar ----------
function healthCheck(cb) {
  http.get(`${BASE}/api/health`, { timeout: 1500 }, (res) => { res.resume(); cb(res.statusCode === 200); })
    .on("error", () => cb(false))
    .on("timeout", function () { this.destroy(); cb(false); });
}

function startSidecar() {
  const repo = path.resolve(__dirname, "..");
  const dev = fs.existsSync(path.join(repo, "burnmeter", "__init__.py"));
  const prodBin = path.join(process.resourcesPath || "", "server",
    process.platform === "win32" ? "burnmeter-server.exe" : "burnmeter-server");
  const cmd = dev ? (process.platform === "win32" ? "python" : "python3") : prodBin;
  const args = dev
    ? ["-m", "burnmeter", "serve", "--host", HOST, "--port", String(PORT), "--no-browser", "--no-shortcut"]
    : ["serve", "--host", HOST, "--port", String(PORT), "--no-browser", "--no-shortcut"];
  try {
    pyProc = spawn(cmd, args, {
      cwd: dev ? repo : path.dirname(prodBin),
      env: { ...process.env, PYTHONPATH: dev ? repo : "", PYTHONUTF8: "1" },
      stdio: "ignore",
      windowsHide: true,
    });
    pyProc.on("error", (e) => console.error("[sidecar] spawn failed:", e.message));
    pyProc.on("exit", (code) => { if (!quitting) console.error("[sidecar] exited:", code); });
  } catch (e) {
    console.error("[sidecar] spawn threw:", e.message);
  }
}

function waitForServer(cb, tries = 120) {
  const probe = () => healthCheck((ok) => {
    if (ok) return cb(true);
    if (--tries > 0) return setTimeout(probe, 500);
    cb(false);
  });
  probe();
}

// ---------- window geometry persistence (position + size + maximized) ----------
function loadState() {
  try { return JSON.parse(fs.readFileSync(STATE_FILE(), "utf-8")); } catch { return null; }
}
function saveState() {
  if (!win) return;
  try {
    const max = win.isMaximized();
    const b = max ? (loadState() || {}) : { ...win.getBounds() };
    fs.writeFileSync(STATE_FILE(), JSON.stringify({ x: b.x, y: b.y, width: b.width, height: b.height, maximized: max }));
  } catch { /* best-effort */ }
}
function visibleOnSomeDisplay(b) {
  if (b == null || b.x == null || b.y == null) return false;
  return screen.getAllDisplays().some((d) => {
    const a = d.workArea;
    return b.x + 60 > a.x && b.x < a.x + a.width - 60 && b.y + 20 > a.y && b.y < a.y + a.height - 60;
  });
}

// ---------- window ----------
function createWindow() {
  const st = loadState();
  const usable = st && visibleOnSomeDisplay(st);
  win = new BrowserWindow({
    width: (usable && st.width) || 1480,
    height: (usable && st.height) || 940,
    ...(usable ? { x: st.x, y: st.y } : {}),
    minWidth: 900,
    minHeight: 600,
    show: false,                                  // no white flash — show on ready
    backgroundColor: "#0F1011",                   // matches --bg-base
    title: "Burnmeter",
    icon: loadIcon(true),
    autoHideMenuBar: true,
    // Premium: hidden native titlebar; Chromium draws overlay window controls in our
    // dark palette; the app's own topbar becomes the drag region (body.electron CSS).
    titleBarStyle: "hidden",
    titleBarOverlay: OVERLAY,
    webPreferences: {
      contextIsolation: true,
      preload: path.join(__dirname, "preload.js"),
      spellcheck: false,
      backgroundThrottling: false,   // black-frame guard: never throttle the first paint
    },
  });
  Menu.setApplicationMenu(null);
  if (st && st.maximized) win.maximize();
  win.once("ready-to-show", () => {
    win.show();
    // Belt & braces: re-assert the dark overlay after show — on some Windows builds the
    // construction-time overlay color is ignored until reapplied (white strip bug).
    try { win.setTitleBarOverlay(OVERLAY); } catch { /* non-Windows / older Electron */ }
    // Black-frame guard #2: some GPUs show the window without ever presenting the first
    // frame on a cold boot — force a compositor repaint shortly after show.
    setTimeout(() => { try { if (win && !win.isDestroyed()) win.webContents.invalidate(); } catch {} }, 600);
    setTimeout(() => { try { if (win && !win.isDestroyed()) win.webContents.invalidate(); } catch {} }, 2500);
  });
  win.loadURL(`${BASE}/`);
  // Inject the drag region on EVERY load — never depend on the server's CSS version.
  win.webContents.on("did-finish-load", () => {
    win.webContents.insertCSS(DRAG_CSS).catch(() => {});
  });
  // Cold-boot races: if the first load failed (sidecar not accepting yet) retry; if the
  // renderer ever dies, bring it back. The window must NEVER sit black.
  win.webContents.on("did-fail-load", (e, code, desc, url, isMainFrame) => {
    if (isMainFrame) setTimeout(() => { try { win.loadURL(`${BASE}/`); } catch {} }, 1200);
  });
  win.webContents.on("render-process-gone", () => {
    setTimeout(() => { try { win.webContents.reload(); } catch {} }, 500);
  });

  ["resized", "moved", "maximize", "unmaximize"].forEach((ev) => win.on(ev, saveState));
  win.on("close", (e) => {
    saveState();
    if (!quitting) { e.preventDefault(); win.hide(); }   // close → tray (meter keeps running)
  });
  win.on("closed", () => { win = null; });

  // external links (burnmeter.dev, GitHub, …) open in the real browser
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("http") && !url.startsWith(BASE)) { shell.openExternal(url); return { action: "deny" }; }
    return { action: "allow" };
  });
  win.webContents.on("will-navigate", (e, url) => {
    if (url.startsWith("http") && !url.startsWith(BASE)) { e.preventDefault(); shell.openExternal(url); }
  });
}

function showWindow() {
  if (win) { win.show(); win.focus(); } else { createWindow(); }
}

// ---------- tray ----------
function createTray() {
  let icon = loadIcon(true);                       // .ico preferred on Windows (crisp tray)
  if (!icon.isEmpty() && process.platform !== "win32") icon = icon.resize({ width: 16, height: 16 });
  tray = new Tray(icon);
  tray.setToolTip(`Burnmeter — ${BASE}`);
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: "Open Burnmeter", click: showWindow },
    { label: "burnmeter.dev", click: () => shell.openExternal("https://burnmeter.dev") },
    { type: "separator" },
    { label: "Quit Burnmeter", click: () => { quitting = true; app.quit(); } },
  ]));
  tray.on("click", showWindow);         // single-click opens (Windows convention)
}

// ---------- lifecycle ----------
nativeTheme.themeSource = "dark";   // dark window chrome + dark UA scrollbars everywhere
app.whenReady().then(() => {
  healthCheck((alreadyUp) => {
    if (!alreadyUp) startSidecar();
    waitForServer((ok) => {
      createTray();
      createWindow();
      if (!ok && win) win.loadURL(`data:text/html,<body style="background:%230F1011;color:%23c7ccd1;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh"><div><h2>Burnmeter server didn't start</h2><p>Try again, or run <code>burnmeter serve</code> manually.</p></div></body>`);
    });
  });
});

app.on("activate", showWindow);                     // macOS dock click
app.on("before-quit", () => { quitting = true; saveState(); });
app.on("window-all-closed", (e) => { /* stay in tray — never auto-quit */ });
app.on("quit", () => {
  if (pyProc) { try { pyProc.kill(); } catch { /* already gone */ } }
});
