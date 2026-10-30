// Burnmeter desktop — Electron shell (production).
//
// Electron owns the window/tray/Chromium; the existing Python backend runs as a
// SIDECAR (the same local web server the pip app uses). Launch order:
//   1. Tray + window appear IMMEDIATELY on a local splash (splash.html) that tracks
//      the server's startup and turns into an error page with Retry if it fails.
//   2. If a Burnmeter server we can TRUST already runs on the port — same version as
//      this shell AND /api/health echoes the token from ~/.burnmeter/server.json — we
//      ATTACH to it instead of spawning a second one. Anything else answering there (an
//      old pip server, a foreign app) is left alone; our sidecar moves to a free port.
//   3. Else spawn the sidecar (dev: repo python; prod: bundled PyInstaller
//      burnmeter-server from process.resourcesPath), identify it, load the dashboard.
// The sidecar writes to ~/.burnmeter/sidecar.log and is respawned with backoff if it dies.
//
// Window model matches the product's: closing the window HIDES to tray (the meter
// keeps collecting); Quit lives in the tray menu. We only kill the sidecar on quit
// if WE spawned it — never someone else's server — and then its whole process tree.
const { app, BrowserWindow, Menu, Tray, shell, nativeImage, nativeTheme, screen, powerMonitor, session, ipcMain } = require("electron");
const { spawnSync } = require("child_process");
const { EventEmitter } = require("events");
const { Worker } = require("worker_threads");
const http = require("http");
const fs = require("fs");
const os = require("os");
const path = require("path");

const HOST = "127.0.0.1";
const PORT = Number(process.env.BURNMETER_PORT || 7654);   // preferred port (same as the shipped app)
const STATE_FILE = () => path.join(app.getPath("userData"), "window-state.json");
const SPLASH = path.join(__dirname, "splash.html");
const BM_DIR = path.join(os.homedir(), ".burnmeter");
const PIDFILE = path.join(BM_DIR, "server.json");          // every Burnmeter server writes it on bind
const SIDECAR_LOG = path.join(BM_DIR, "sidecar.log");
const REPO = path.resolve(__dirname, "..");
const DEV = fs.existsSync(path.join(REPO, "burnmeter", "__init__.py"));

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

// Content-Security-Policy for every document the sidecar serves. Allowed for now,
// tighten as the dashboard drops them: inline on* handlers ('unsafe-inline') and the
// version check that reads raw.githubusercontent.com. Chart.js is bundled (/static).
const CSP = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  "connect-src 'self' https://raw.githubusercontent.com",
  "object-src 'none'",
  "frame-src 'none'",
  "worker-src 'none'",
  "base-uri 'none'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

let win = null;
let tray = null;
let quitting = false;
let server = null;        // the server on screen: { port, base, owned, at } — null while booting
let pyProc = null;        // sidecar process IF we spawned it (null = attached / not started)
let failures = 0;         // consecutive sidecar deaths → respawn backoff
let bootTimer = null;     // pending identify poll / respawn
let attachTimer = null;   // health ping while attached to a server that isn't ours
let splashState = { state: "starting", title: "Starting Burnmeter…", detail: "" };

// ---------- single instance: a real app has ONE window ----------
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => { if (app.isReady()) showWindow(); });
}

// ---------- sidecar log: ~/.burnmeter/sidecar.log ----------
function log(msg) {
  const line = `[${new Date().toISOString()}] [shell] ${msg}\n`;
  try { fs.mkdirSync(BM_DIR, { recursive: true }); fs.appendFileSync(SIDECAR_LOG, line); } catch { /* best-effort */ }
  if (!app.isPackaged) process.stdout.write(line);
}
// The sidecar gets the file itself (not a pipe), so its output survives a shell crash
// and a dead shell can never wedge it on a full pipe.
function openLogFd() {
  try {
    fs.mkdirSync(BM_DIR, { recursive: true });
    try { if (fs.statSync(SIDECAR_LOG).size > 2 * 1024 * 1024) fs.renameSync(SIDECAR_LOG, `${SIDECAR_LOG}.1`); } catch { /* no log yet */ }
    return fs.openSync(SIDECAR_LOG, "a");
  } catch { return "ignore"; }
}

// ---------- sidecar: health + identity ----------
function getHealth(port, cb) {              // → parsed /api/health JSON, or null
  let done = false;
  const finish = (v) => { if (!done) { done = true; cb(v); } };
  const req = http.get({ host: HOST, port, path: "/api/health", timeout: 1500, agent: false }, (res) => {
    if (res.statusCode !== 200) { res.resume(); return finish(null); }
    let body = "";
    res.setEncoding("utf8");
    res.on("data", (c) => { body += c; if (body.length > 65536) req.destroy(); });
    res.on("end", () => { try { const j = JSON.parse(body); finish(j && typeof j === "object" ? j : null); } catch { finish(null); } });
    res.on("close", () => finish(null));
  });
  req.on("timeout", () => req.destroy());
  req.on("error", () => finish(null));
}
function readPidfile() {
  try { const j = JSON.parse(fs.readFileSync(PIDFILE, "utf-8")); return j && typeof j === "object" ? j : null; } catch { return null; }
}
function pidfileMtime() {
  try { return fs.statSync(PIDFILE).mtimeMs; } catch { return 0; }
}
function expectedVersion() {                // what our own sidecar reports; releases bump both
  if (!DEV) return app.getVersion();
  try { return /__version__\s*=\s*"([^"]+)"/.exec(fs.readFileSync(path.join(REPO, "burnmeter", "__init__.py"), "utf-8"))[1]; } catch { return null; }
}

// CreateProcess on an unsigned exe can block until the antivirus has scanned it — ~10 s
// measured (Kaspersky), even for a sidecar that had run before. Spawning from a worker thread
// keeps the splash, window and tray responsive meanwhile; the emitter mimics a ChildProcess.
const SPAWNER = `
  const { parentPort, workerData: w } = require("worker_threads");
  try {
    const c = require("child_process").spawn(w.cmd, w.args, w.opts);
    if (c.pid) parentPort.postMessage({ pid: c.pid });
    c.on("error", (e) => parentPort.postMessage({ error: e.message }));
    c.on("exit", (code, signal) => parentPort.postMessage({ exit: true, code, signal }));
  } catch (e) { parentPort.postMessage({ error: e.message }); }
`;
function spawnOffThread(cmd, args, opts) {
  const proc = Object.assign(new EventEmitter(), { pid: undefined, exitCode: null, signalCode: null });
  const worker = new Worker(SPAWNER, { eval: true, workerData: { cmd, args, opts } });
  worker.on("message", (m) => {
    if (m.pid) { proc.pid = m.pid; proc.emit("spawn"); }
    else if (m.error) proc.emit("error", new Error(m.error));
    else if (m.exit) { proc.exitCode = m.code; proc.signalCode = m.signal; proc.emit("exit", m.code, m.signal); }
  });
  worker.on("error", (e) => proc.emit("error", e));
  return proc;
}

// ---------- sidecar: find or start a server we trust ----------
function boot() {
  clearTimeout(bootTimer);
  if (quitting || server || pyProc) return;
  getHealth(PORT, (h) => {
    if (quitting || server || pyProc) return;      // a concurrent boot (Retry) got there first
    const pf = readPidfile();
    if (h && h.ok === true && h.version === expectedVersion()
        && pf && Number(pf.port) === PORT && pf.token && h.token === pf.token) {
      return ready({ port: PORT, owned: false });
    }
    if (h) log(`port ${PORT} answers as ${h.version ? `Burnmeter v${h.version}` : "something else"} — not ours, starting our own sidecar`);
    startSidecar();
  });
}

function startSidecar() {
  const prodBin = path.join(process.resourcesPath || "", "server",
    process.platform === "win32" ? "burnmeter-server.exe" : "burnmeter-server");
  const cmd = DEV ? (process.platform === "win32" ? "python" : "python3") : prodBin;
  const args = [...(DEV ? ["-m", "burnmeter"] : []),
    "serve", "--host", HOST, "--port", String(PORT), "--no-browser", "--no-shortcut"];
  log(`starting sidecar: ${cmd} ${args.join(" ")}`);
  const out = openLogFd();
  const startedAt = Date.now();
  const child = spawnOffThread(cmd, args, {
    cwd: DEV ? REPO : path.dirname(prodBin),
    env: { ...process.env, PYTHONPATH: DEV ? REPO : "", PYTHONUTF8: "1", PYTHONUNBUFFERED: "1" },
    stdio: ["ignore", out, out],
    windowsHide: true,
    detached: process.platform !== "win32",   // own process group → killSidecar() reaps its workers
  });
  let logOpen = typeof out === "number";      // the child holds its own handle once spawned
  const closeLog = () => { if (logOpen) { logOpen = false; try { fs.closeSync(out); } catch { /* already closed */ } } };
  child.once("spawn", closeLog);
  child.once("error", closeLog);
  pyProc = child;
  let gone = false;
  const onGone = (why) => { if (!gone) { gone = true; sidecarGone(child, why); } };
  child.on("error", (e) => onGone(`couldn't start (${e.message})`));
  child.on("exit", (code, sig) => onGone(`stopped (exit code ${code}${sig ? `, ${sig}` : ""})`));
  identify(child, startedAt);
}

// Ours = the pidfile names the process we spawned AND the server on the pidfile's port
// echoes the pidfile's per-process token. Port-agnostic on purpose: when 7654 is busy the
// sidecar binds the next free port and records it there.
function identify(child, startedAt) {
  const wait = () => {
    const waited = Date.now() - startedAt;
    if (waited > 60000) {
      if (splashState.state !== "error") showSplash("error", "Burnmeter's server isn't answering", "It has been starting for over a minute. Burnmeter keeps trying in the background.");
    } else if (waited > 15000 && splashState.title !== "Still starting…") {
      showSplash("starting", "Still starting…", "The local server is taking longer than usual.");
    }
    bootTimer = setTimeout(tick, 500);
  };
  const tick = () => {
    if (quitting || child !== pyProc) return;
    const pf = readPidfile();
    const mine = pf && pf.token && Number(pf.port) > 0 && (Number(pf.pid) === child.pid
      // dev: `python` may be a venv launcher whose child is the real server
      || (DEV && pf.version === expectedVersion() && pidfileMtime() >= startedAt));
    if (!mine) return wait();
    getHealth(Number(pf.port), (h) => {
      if (quitting || child !== pyProc) return;
      if (h && h.ok === true && h.token === pf.token) return ready({ port: Number(pf.port), owned: true, pid: child.pid });
      wait();
    });
  };
  tick();
}

function ready(srv) {
  clearTimeout(bootTimer);
  server = { ...srv, base: `http://${HOST}:${srv.port}`, at: Date.now() };
  log(srv.owned ? `sidecar pid ${srv.pid} ready on ${server.base}` : `attached to the Burnmeter already running on ${server.base}`);
  if (tray) tray.setToolTip(`Burnmeter — ${server.base}`);
  if (!srv.owned) watchAttached();
  if (win && !win.isDestroyed()) win.loadURL(`${server.base}/`);
}

function sidecarGone(child, why) {
  log(`sidecar${child && child.pid ? ` pid ${child.pid}` : ""} ${why}`);
  if (child && child !== pyProc) return;         // one we killed on purpose (Retry / quit)
  pyProc = null;
  if (quitting) return;
  const upLong = !!server && server.owned && Date.now() - server.at > 60000;
  if (server && server.owned) server = null;
  failures = upLong ? 1 : failures + 1;            // a long healthy run earns a fresh backoff
  const delay = Math.min(30000, 1000 * 2 ** (failures - 1));   // 1, 2, 4, 8, 16, 30 s
  if (failures >= 3) showSplash("error", "Burnmeter's server keeps stopping", `It ${why}. Trying again in ${Math.round(delay / 1000)} s.`);
  else showSplash("starting", "Restarting Burnmeter's server…", `It ${why}.`);
  clearTimeout(bootTimer);
  bootTimer = setTimeout(boot, delay);
}

// An attached server can vanish without telling us (the user quit their pip tray) —
// ping it, and fall back to our own sidecar once it's really gone.
function watchAttached() {
  clearInterval(attachTimer);
  let misses = 0;
  attachTimer = setInterval(() => {
    if (quitting || !server || server.owned) return clearInterval(attachTimer);
    const port = server.port;
    getHealth(port, (h) => {
      if (!server || server.owned || server.port !== port) return;
      if (h && h.ok === true) { misses = 0; return; }
      if (++misses < 3) return;
      clearInterval(attachTimer);
      log(`attached server on ${server.base} is gone`);
      server = null;
      showSplash("starting", "Reconnecting…");
      boot();
    });
  }, 10000);
}

function retry() {
  log("retry requested");
  clearTimeout(bootTimer);
  failures = 0;
  if (server) { if (win && !win.isDestroyed()) win.loadURL(`${server.base}/`); return; }
  killSidecar();                                   // one that never answered — start over
  showSplash("starting", "Starting Burnmeter…");
  boot();
}

// Kill the sidecar AND everything it started — a mid-build `_worker` would otherwise
// outlive the app. Windows has no process groups, so taskkill walks the tree.
function killSidecar() {
  const child = pyProc;
  pyProc = null;                                   // its exit is expected now: no respawn
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  if (!child.pid) { child.once("spawn", () => killTree(child.pid)); return; }   // still in CreateProcess
  killTree(child.pid);
}
function killTree(pid) {
  if (process.platform === "win32") {
    const taskkill = path.join(process.env.SystemRoot || "C:\\Windows", "System32", "taskkill.exe");
    spawnSync(taskkill, ["/PID", String(pid), "/T", "/F"], { windowsHide: true, timeout: 5000 });
  } else {
    try { process.kill(-pid, "SIGTERM"); } catch { try { process.kill(pid, "SIGTERM"); } catch { /* already gone */ } }
  }
}

// ---------- splash (local page; the preload gives it a tiny bridge, nothing else) ----------
function showSplash(state, title, detail = "") {
  splashState = { state, title, detail, log: SIDECAR_LOG };
  if (!win || win.isDestroyed()) return;
  if (win.webContents.getURL().startsWith("file:")) win.webContents.send("splash:state", splashState);
  else win.loadFile(SPLASH);                       // it asks for splashState once loaded
}
const fromSplash = (e) => !!win && e.sender === win.webContents && !!e.senderFrame
  && !e.senderFrame.parent && e.senderFrame.url.startsWith("file:");
ipcMain.handle("splash:state", (e) => (fromSplash(e) ? splashState : null));
ipcMain.on("splash:retry", (e) => { if (fromSplash(e)) retry(); });

// ---------- navigation guards (every webContents, present and future) ----------
// Origin equality, not a string prefix: "http://127.0.0.1:7654@evil.example/" and
// "http://127.0.0.1:76540/" both START with the app's address.
function isAppUrl(u) {
  if (!server) return false;
  try { return new URL(u).origin === server.base; } catch { return false; }
}
// Only plain web links leave the app: never file:, ms-*: or other OS protocol handlers,
// and never a URL with embedded credentials (the user@host disguise).
function openExternal(u) {
  let url;
  try { url = new URL(u); } catch { return; }
  if ((url.protocol !== "https:" && url.protocol !== "http:") || url.username || url.password) {
    log(`blocked navigation to ${u}`);
    return;
  }
  shell.openExternal(url.href).catch(() => {});
}
app.on("web-contents-created", (_e, contents) => {
  contents.setWindowOpenHandler(({ url }) => {
    if (!isAppUrl(url)) openExternal(url);         // external links (burnmeter.dev, GitHub, …) → real browser
    return { action: "deny" };                     // no in-app popups, ever
  });
  contents.on("will-navigate", (e) => {
    if (isAppUrl(e.url)) return;
    e.preventDefault();
    openExternal(e.url);
  });
  contents.on("will-frame-navigate", (e) => { if (!e.isMainFrame && !isAppUrl(e.url)) e.preventDefault(); });
  contents.on("will-redirect", (e) => { if (!isAppUrl(e.url)) e.preventDefault(); });
  contents.on("will-attach-webview", (e) => e.preventDefault());
});

// ---------- session: permissions + CSP ----------
function hardenSession(ses) {
  // No camera, mic, geolocation, clipboard, HID, … Our own dashboard gets exactly two:
  // notifications (its limit-alert toggle warns through them while the window sits in the
  // tray) and fullscreen (a browser grants it without asking; on Electron 45 a DENIED
  // fullscreen request leaves the page hanging and took the renderer down in testing).
  const GRANTED = new Set(["notifications", "fullscreen"]);
  const allowed = (perm, url) => GRANTED.has(perm) && isAppUrl(url);
  ses.setPermissionRequestHandler((_wc, perm, cb, details) => {
    const ok = allowed(perm, details && details.requestingUrl);
    if (!ok) log(`denied permission "${perm}" for ${details && details.requestingUrl}`);
    cb(ok);
  });
  ses.setPermissionCheckHandler((_wc, perm, origin) => allowed(perm, origin));
  ses.setDevicePermissionHandler(() => false);
  ses.webRequest.onHeadersReceived((d, cb) => {
    if ((d.resourceType === "mainFrame" || d.resourceType === "subFrame") && isAppUrl(d.url)) {
      return cb({ responseHeaders: { ...d.responseHeaders, "Content-Security-Policy": [CSP] } });
    }
    cb({});
  });
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
      preload: path.join(__dirname, "preload.js"),
      sandbox: true,                 // the dashboard is a plain web page: no Node, OS sandbox
      contextIsolation: true,
      nodeIntegration: false,
      webSecurity: true,
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
  if (server) win.loadURL(`${server.base}/`); else win.loadFile(SPLASH);
  // Inject the drag region on EVERY dashboard load — never depend on the server's CSS version.
  win.webContents.on("did-finish-load", () => {
    if (isAppUrl(win.webContents.getURL())) win.webContents.insertCSS(DRAG_CSS).catch(() => {});
  });
  // Cold-boot races: if a dashboard load failed (sidecar not accepting yet) retry; if the
  // renderer ever dies, bring it back. The window must NEVER sit black.
  win.webContents.on("did-fail-load", (e, code, desc, url, isMainFrame) => {
    if (isMainFrame && code !== -3 && isAppUrl(url)) {
      setTimeout(() => { try { if (server && win && !win.isDestroyed()) win.loadURL(`${server.base}/`); } catch {} }, 1200);
    }
  });
  win.webContents.on("render-process-gone", () => {
    setTimeout(() => { try { win.webContents.reload(); } catch {} }, 500);
  });

  ["resized", "moved", "maximize", "unmaximize"].forEach((ev) => win.on(ev, saveState));
  win.on("close", (e) => {
    saveState();
    if (!quitting) { e.preventDefault(); win.hide(); }   // close → tray (meter keeps running)
  });
  win.on("show", () => setTimeout(wakeRenderer, 250));   // back from tray → data fresh NOW
  win.on("restore", () => setTimeout(wakeRenderer, 250));
  win.on("closed", () => { win = null; });
}

function showWindow() {
  if (win) { if (win.isMinimized()) win.restore(); win.show(); win.focus(); } else { createWindow(); }
}

// ---------- tray ----------
function createTray() {
  let icon = loadIcon(true);                       // .ico preferred on Windows (crisp tray)
  if (!icon.isEmpty() && process.platform !== "win32") icon = icon.resize({ width: 16, height: 16 });
  tray = new Tray(icon);
  tray.setToolTip(server ? `Burnmeter — ${server.base}` : "Burnmeter");
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: "Open Burnmeter", click: showWindow },
    { label: "burnmeter.dev", click: () => shell.openExternal("https://burnmeter.dev") },
    { type: "separator" },
    { label: "Quit Burnmeter", click: () => { quitting = true; app.quit(); } },
  ]));
  tray.on("click", showWindow);         // single-click opens (Windows convention)
}

// ---------- lifecycle ----------
// Wake-from-sleep recovery: a fetch in flight at suspend time can hang forever
// against a half-open loopback socket. The page has its own watchdogs (__bmWake,
// zombie takeover), but OS resume/unlock are signals only the shell sees reliably —
// poke the page the moment the machine wakes so the numbers are never stale-frozen.
// Guarded call: a page from an older server (no __bmWake) is a safe no-op.
function wakeRenderer() {
  if (!win || win.isDestroyed()) return;
  win.webContents.executeJavaScript("window.__bmWake && window.__bmWake()").catch(() => {});
}

nativeTheme.themeSource = "dark";   // dark window chrome + dark UA scrollbars everywhere
app.whenReady().then(() => {
  if (!gotLock) return;
  hardenSession(session.defaultSession);
  powerMonitor.on("resume", () => setTimeout(wakeRenderer, 1500));        // let the loopback settle
  powerMonitor.on("unlock-screen", () => setTimeout(wakeRenderer, 500));
  createTray();
  createWindow();                     // splash now — the dashboard replaces it once the server answers
  boot();
});

app.on("activate", showWindow);                     // macOS dock click
app.on("before-quit", (e) => {
  quitting = true;
  saveState();
  // A sidecar still inside CreateProcess has no pid to kill yet — hold the quit until it does,
  // or it would start after we're gone and run orphaned.
  const child = pyProc;
  if (child && !child.pid && child.exitCode === null) {
    e.preventDefault();
    const again = () => setImmediate(() => app.quit());
    child.once("spawn", again);
    child.once("error", again);
  }
});
app.on("window-all-closed", (e) => { /* stay in tray — never auto-quit */ });
app.on("quit", () => {
  clearTimeout(bootTimer);
  clearInterval(attachTimer);
  killSidecar();
});
