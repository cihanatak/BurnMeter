// Burnmeter — dashboard frontend (modern rebuild 2026-05-30)
// Local /api/report (source=claude|codex). Numbers are the hero.

// ---------- localStorage migration: ccmeter_* → burnmeter_* (eski ayarlar korunur, tek seferlik) ----------
(function () { try {
  if (localStorage.getItem("burnmeter_ls_migrated")) return;
  // ÖNCE tüm eski anahtarları topla — taşıma sırasında setItem/removeItem localStorage'ı
  // re-index ettiği için index-tabanlı okuma key atlar (bazı ayarlar kaybolurdu).
  const old = [];
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    if (k && k.indexOf("ccmeter_") === 0) old.push(k);
  }
  for (const k of old) {
    const nk = "burnmeter_" + k.slice(8);
    if (localStorage.getItem(nk) === null) localStorage.setItem(nk, localStorage.getItem(k));
    localStorage.removeItem(k);
  }
  localStorage.setItem("burnmeter_ls_migrated", "1");
} catch (e) {} })();

// ---------- helpers ----------
const $ = (id) => document.getElementById(id);
const fmtMoney = (n) => "$" + (Number(n) || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtMoney0 = (n) => "$" + Math.round(Number(n) || 0).toLocaleString("en-US");
const fmtInt = (n) => (Number(n) || 0).toLocaleString("en-US");
const fmtCompact = (n) => {
  n = Number(n) || 0;
  if (Math.abs(n) >= 1e9) return (n / 1e9).toFixed(2) + "B";
  if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(2) + "M";
  if (Math.abs(n) >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return String(Math.round(n));
};
const fmtPct = (r) => (r == null ? "—" : (Number(r) * 100).toFixed(1) + "%");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function relTime(iso) {
  if (!iso) return "—";
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 0) return "in a moment";
  if (diff < 60) return diff + "s ago";
  if (diff < 3600) return Math.floor(diff / 60) + "m ago";
  if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
  const d = Math.floor(diff / 86400);
  if (d < 7) return d + "d ago";
  return new Date(iso).toISOString().slice(0, 10);
}
function resetIn(epochSec) {
  if (!epochSec) return "";
  const rem = Math.max(0, epochSec - Date.now() / 1000);
  const h = Math.floor(rem / 3600), m = Math.floor((rem % 3600) / 60);
  return h >= 1 ? `${h}h ${m}m` : `${m}m`;
}
function modelToFamily(m) {
  m = (m || "").toLowerCase();
  if (m.includes("opus")) return "opus";
  if (m.includes("sonnet")) return "sonnet";
  if (m.includes("haiku")) return "haiku";
  if (m.includes("fable") || m.includes("mythos")) return "fable";
  if (m.includes("codex")) return "gpt-codex";
  if (m.includes("mini")) return "gpt-mini";
  if (m.includes("gpt-5.5") || m.includes("gpt5.5")) return "gpt-55";
  if (m.includes("gpt-5.4") || m.includes("gpt5.4")) return "gpt-54";
  if (m.includes("gpt") || m.startsWith("o1") || m.startsWith("o3")) return "gpt-55";
  return "unknown";
}
function modelDisplay(m) {
  if (!m) return "—";
  const raw = String(m); if (raw.startsWith("<")) return raw.replace(/[<>]/g, "");
  const cl = raw.toLowerCase().match(/(opus|sonnet|haiku)-(\d+)-(\d+)/);
  if (cl) return cl[1][0].toUpperCase() + cl[1].slice(1) + " " + cl[2] + "." + cl[3];
  const fb = raw.toLowerCase().match(/(fable|mythos)-?(\d+)/);
  if (fb) return fb[1][0].toUpperCase() + fb[1].slice(1) + " " + fb[2];
  const g = raw.toLowerCase().match(/gpt-?(\d+(?:\.\d+)?)(-?(codex|mini))?/);
  if (g) return "GPT-" + g[1] + (g[3] === "codex" ? " Codex" : g[3] === "mini" ? " mini" : "");
  return raw;
}
const sevClass = (pct) => pct >= 90 ? "bad" : pct >= 70 ? "warn" : "good";

// ---------- data ----------
window.__source = localStorage.getItem("burnmeter_source") || "claude";
window.__charts = {};
window.__reportCache = {};   // per-source client cache → INSTANT tab switches (SWR)
window.__renderSig = null;   // signature of what's painted now → skip redundant re-renders
// Cheap content signature: changes only when new records get parsed (NOT on every
// stale re-serve), so an unchanged report never triggers a needless full repaint.
function repSig(rep){
  if (!rep) return "";
  if (rep._combined) return "both:" + repSig(rep.claude) + "|" + repSig(rep.codex);
  return (rep.record_count||0) + ":" + ((rep._meta && rep._meta.files_scanned) || 0) + ":" + ((rep._meta && rep._meta.source) || "");
}

async function loadReportFor(src, force) {
  const p = new URLSearchParams();
  if (force) p.set("refresh", "1");
  if (src !== "claude") p.set("source", src);
  const qs = p.toString();
  const r = await fetch("/api/report" + (qs ? "?" + qs : ""));
  if (!r.ok) throw new Error("fetch " + r.status);
  return r.json();
}
const loadReport = (force) => loadReportFor(window.__source, force);

async function refresh(force = false) {
  if (window.__refreshInFlight && !force) return;
  window.__refreshInFlight = true;
  try {
    if (window.__source === "both") {
      const [cl, cx] = await Promise.all([loadReportFor("claude", force), loadReportFor("codex", force)]);
      if (window.__source !== "both") return;               // switched away mid-fetch
      window.__reportCache.claude = cl; window.__reportCache.codex = cx;
      window.__lastReport = { _combined: true, claude: cl, codex: cx };
      const sig = repSig(window.__lastReport);
      if (sig !== window.__renderSig) { renderCombined(cl, cx); window.__renderSig = sig; }
      else { paintCombinedActive("claude", cl); paintCombinedActive("codex", cx); }  // canlı kart HER refresh'te tazelenir
      window.__lastRefreshMs = Date.now();
      return;
    }
    const rep = await loadReport(force);
    if ((rep._meta?.source || "claude") !== window.__source) return; // stale
    window.__reportCache[rep._meta?.source || "claude"] = rep;
    window.__lastReport = rep;
    const sig = repSig(rep);
    if (sig !== window.__renderSig) { render(rep); window.__renderSig = sig; }
    else { renderActiveModel(rep); }                       // canlı kart HER refresh'te tazelenir
    window.__lastRefreshMs = Date.now();
  } catch (e) {
    console.error(e);
    $("last-updated").textContent = "failed to load: " + e.message;
  } finally {
    window.__refreshInFlight = false;
  }
}

// Paint a source from the client cache INSTANTLY (no network) so tab switches are
// immediate; refresh() then silently revalidates in the background (SWR pattern).
function renderFromCache(src) {
  const C = window.__reportCache || {};
  if (src === "both") {
    if (C.claude && C.codex) {
      window.__lastReport = { _combined: true, claude: C.claude, codex: C.codex };
      renderCombined(C.claude, C.codex);
      window.__renderSig = repSig(window.__lastReport);
      return true;
    }
    return false;
  }
  if (C[src]) {
    window.__lastReport = C[src];
    render(C[src]);
    window.__renderSig = repSig(C[src]);
    return true;
  }
  return false;
}

// ---------- render orchestrator ----------
// Device badge — only meaningful when the user has 2+ machines (mirror/sync). For the
// single-machine majority it's redundant noise that mislabeled their own data (e.g. a
// laptop's local data tagged "MAC"), so hide it. window.__multiDevice is set per render.
function deviceBadge(dev) {
  if (!window.__multiDevice || !dev) return "";
  const d = String(dev).replace(/[^a-z0-9_-]/gi, "");
  return d ? `<span class="badge ${d}">${esc(d)}</span>` : "";
}

// "where is this model running" line for the LIVE card — project + device, always
// shown (the device badge is hidden on single-device, but here the user explicitly
// wants to see which project and which PC).
function deviceName(dev) {
  return ({ pc: "PC", mac: "Mac", linux: "Linux" }[String(dev || "").toLowerCase()] || dev || "");
}
function liveWhere(m) {
  const dev = deviceName(m.device);
  const proj = m.project ? esc(m.project) : "unknown project";
  return `<span class="am-proj"><span class="am-proj-icon">📂</span>${proj}${dev ? `<span class="am-dev">${esc(dev)}</span>` : ""}</span>`;
}

// ---------- in-dashboard update check ----------
// Users install via `pip install git+…` and otherwise have no way to know a new version
// shipped. On load we check the version on GitHub (a single GET to a public file — no
// user data sent) and show a small "↑ Update" pill if newer. Fail-silent when offline.
function _verCmp(a, b) {
  const pa = String(a).split(".").map(n => parseInt(n, 10) || 0);
  const pb = String(b).split(".").map(n => parseInt(n, 10) || 0);
  for (let i = 0; i < 3; i++) { const d = (pa[i] || 0) - (pb[i] || 0); if (d) return d > 0 ? 1 : -1; }
  return 0;
}
// Always-present "Check for updates" affordance (matches the tray's item). The
// pill shows "↻ Check for updates" by default; an auto- or manual check flips it to
// a green "↑ Update to vX" when newer, or flashes "✓ Up to date" on a manual check.
function _showCheckAffordance(current) {
  const el = $("update-link"); if (!el) return;
  el.className = "update-pill";
  el.textContent = "↻ Check for updates";
  el.title = "Check GitHub for a newer Burnmeter version";
  el.style.display = "";
  el.onclick = (ev) => { ev.preventDefault(); checkForUpdate(current, true); };
}
function _showUpdateAvailable(latest, current) {
  const el = $("update-link"); if (!el) return;
  el.className = "update-pill has-update";
  el.textContent = `↑ Update to v${latest}`;
  el.href = "https://github.com/cihanatak/BurnMeter";
  el.title = `New version v${latest} is available (you have v${current}). Click to install.`;
  el.style.display = "";
  el.onclick = (ev) => { ev.preventDefault(); doDashboardUpdate(latest); };
}
function _flashPill(msg, current) {
  const el = $("update-link"); if (!el) return;
  el.className = "update-pill";
  el.textContent = msg;
  el.onclick = (ev) => ev.preventDefault();
  setTimeout(() => _showCheckAffordance(current), 2200);
}
async function checkForUpdate(current, manual = false) {
  if (!current || current === "?") return;
  if (!manual && window.__updateChecked) return;   // auto: once per load (periodic resets it)
  window.__updateChecked = true;
  const el = $("update-link");
  if (manual && el) { el.className = "update-pill"; el.textContent = "↻ Checking…"; el.onclick = (ev) => ev.preventDefault(); }
  try {
    const r = await fetch("https://raw.githubusercontent.com/cihanatak/BurnMeter/main/burnmeter/__init__.py", { cache: "no-store" });
    if (!r.ok) throw new Error("http " + r.status);
    const m = (await r.text()).match(/__version__\s*=\s*["']([\d.]+)["']/);
    const latest = m && m[1];
    if (latest && _verCmp(latest, current) > 0) _showUpdateAvailable(latest, current);
    else if (manual) _flashPill(`✓ Up to date (v${current})`, current);
    else _showCheckAffordance(current);
  } catch (e) {
    if (manual) _flashPill("Check failed — offline?", current);
    /* auto: leave the affordance as-is (local-first, fail silent) */
  }
}

// One-click update straight from the dashboard: POST to the local server, which
// runs `pip install --upgrade git+…`. Then the user just restarts Burnmeter.
async function doDashboardUpdate(latest) {
  const el = $("update-link"); if (!el || el.dataset.busy === "1") return;
  el.dataset.busy = "1";
  const orig = el.textContent;
  el.textContent = "⏳ Updating…";
  try {
    const r = await fetch("/api/update", { method: "POST", headers: { "X-Burnmeter-Update": "1" } });
    const d = await r.json().catch(() => ({}));
    if (r.ok && d.ok) {
      el.textContent = `⏳ Updating to v${latest} — restarting…`;
      el.title = "Burnmeter is restarting itself; this page will reload on the new version.";
      el.onclick = (ev) => ev.preventDefault();
      waitForRestartAndReload(latest);     // server restarts itself; reload when back
    } else {
      el.textContent = orig; el.dataset.busy = "";
      alert("Update failed:\n" + (d.message || ("HTTP " + r.status))
            + "\n\nUpdate manually:\npip install --force-reinstall --no-cache-dir git+https://github.com/cihanatak/BurnMeter");
    }
  } catch (e) {
    el.textContent = orig; el.dataset.busy = "";
    alert("Burnmeter doesn't seem to be running anymore — this page is stale.\n\n"
        + "Open Burnmeter again (the 🔥 tray icon, or run `burnmeter tray`), then "
        + "update from there.\n\nOr update manually:\n"
        + "pip install --force-reinstall --no-cache-dir git+https://github.com/cihanatak/BurnMeter");
  }
}

// After a one-click update the server restarts itself on the same port. Wait for
// it to go down and come back, then reload THIS tab onto the new version.
async function waitForRestartAndReload(latest) {
  let downSeen = false;
  for (let i = 0; i < 60; i++) {
    await new Promise(r => setTimeout(r, 1000));
    try {
      const r = await fetch("/api/health", { cache: "no-store" });
      if (r.ok) {
        const d = await r.json().catch(() => ({}));
        if (downSeen || d.version === latest) { location.reload(); return; }
      }
    } catch (e) { downSeen = true; }   // server is down → mid-restart
  }
  const el = $("update-link"); if (el) el.textContent = `✓ Updated to v${latest} — reload`;
}

function render(rep) {
  { const cv = $("combined-view"); if (cv) cv.style.display = "none";
    const mn = document.querySelector("main"); if (mn) mn.style.display = ""; }
  window.__multiDevice = Object.keys(rep.by_device || {}).length > 1;
  const src = rep._meta?.source || "claude";
  const isCodex = src === "codex";
  document.documentElement.style.setProperty("--brand", isCodex ? "var(--brand-codex)" : "var(--brand-claude)");
  document.documentElement.style.setProperty("--brand-soft", isCodex ? "var(--brand-codex-soft)" : "var(--brand-claude-soft)");
  $("app-name").textContent = "Burnmeter";
  $("brand-logo").textContent = isCodex ? "⌬" : "◔";
  document.title = "Burnmeter — AI coding fuel gauge";
  $("version").textContent = "v" + (rep._meta?.version || "?");
  { const v = rep._meta?.version; if (v) { _showCheckAffordance(v); checkForUpdate(v); } }
  $("data-source").textContent = `${rep._meta?.files_scanned ?? "?"} files · ${fmtInt(rep.record_count || 0)} records`;

  renderWelcome(rep);
  renderSpeedometer(rep, isCodex);
  renderHeroAside(rep, isCodex);
  renderKPIs(rep, isCodex);
  renderEfficiency(rep, isCodex);
  renderCache(rep);
  renderBudget(rep, isCodex);
  renderSyncDevices();
  renderTrend(rep, isCodex);
  renderModels(rep);
  renderDaily(rep);
  renderProjects(rep);
  renderHeatmap(rep);
  renderRecent(rep);
  renderModelTable(rep);
  renderBehavior(rep);
  renderTools(rep);
  renderActiveModel(rep);
  checkAlerts(rep, isCodex);
}

// ---------- first-run WELCOME (onboarding) ----------
// New users land on ~18 mostly-empty panels with no idea why. While history is
// thin, show a friendly banner ON TOP (no panels removed) that either tells a
// zero-data user how to get data, or explains that the gauges are market-normal
// (still "calibrating") until there's enough personal history. Dismissible once.
function renderWelcome(rep) {
  const el = $("welcome-card"); if (!el) return;
  const n = rep.record_count || 0;
  const dismissed = localStorage.getItem("burnmeter_welcome_dismissed") === "1";
  if (n >= 200 || dismissed) { el.style.display = "none"; return; }
  const tool = (rep._meta?.source || "claude") === "codex" ? "Codex" : "Claude Code";
  let lead, body;
  if (n === 0) {
    lead = "No usage data yet — let's fix that";
    body = `Burnmeter reads your local <b>${tool}</b> logs — nothing leaves your machine. `
         + `Start a ${tool} session and your tokens, cost and burn rate appear here automatically. `
         + `This page refreshes on its own, so you can just leave it open.`;
  } else {
    lead = "Welcome — you're just getting started";
    body = `Burnmeter has seen <b>${fmtInt(n)}</b> ${tool} ${n === 1 ? "record" : "records"} so far. `
         + `The gauges use <b>market-normal</b> thresholds while they calibrate — once there's enough `
         + `of your own history they switch to your personal baseline, so early readings are a sensible `
         + `reference, not a verdict on your habits.`;
  }
  el.innerHTML =
    `<div class="wc-icon">🔥</div>`
    + `<div class="wc-text"><div class="wc-lead">${lead}</div><div class="wc-body">${body}</div></div>`
    + `<button class="wc-dismiss" id="wc-dismiss" title="hide this">Got it ✕</button>`;
  el.style.display = "";
  const btn = $("wc-dismiss");
  if (btn) btn.onclick = () => {
    localStorage.setItem("burnmeter_welcome_dismissed", "1");
    el.style.display = "none";
  };
}

// ---------- live ACTIVE MODEL (canlı çalışan model · 15dk/1sa/5sa) ----------
// Eski sürümde çok sevilen kutu: o an hangi model(ler) çalışıyor, ne hızla.
// Veri rep.live_active_models_by_window[15|60|300|1440] — her model: tokens_per_min,
// messages, cost_per_hour, seconds_since_last (canlı mı). En yakın = "şu an çalışan".
// "ne zaman görüldü" etiketi — canlıysa yeşil nabız + şimdi (tickLive saniyede bir günceller)
function seenLabel(s) {
  return s < 120 ? `<span class="lp"></span>now`
    : s < 3600 ? `${Math.round(s / 60)}m ago` : `${(s / 3600).toFixed(1)}h ago`;
}
// SANİYELİK canlılık tiki: tüm am-row'lardaki (tek + İkisi) "şimdi"/dot durumunu
// last_seen'den client-side yeniden hesaplar — backend refresh'ini beklemez.
function tickLive() {
  const now = Date.now();
  document.querySelectorAll(".am-row[data-ls]").forEach(row => {
    const ls = row.getAttribute("data-ls"); if (!ls) return;
    const s = (now - new Date(ls).getTime()) / 1000; if (!isFinite(s)) return;
    const live = s < 120;
    const dot = row.querySelector(".am-dot"); if (dot) dot.classList.toggle("live", live);
    const seen = row.querySelector(".am-seen");
    if (seen) { seen.classList.toggle("live", live); seen.innerHTML = seenLabel(Math.max(0, s)); }
  });
}
// Re-render the burn-rate gauge from the cached report every few seconds so it
// reflects the BROWSER's now — decays as time passes, drops to $0 when idle —
// without a server round-trip. renderSpeedometer / paintBurnGauge are draw-only
// (no listeners attached), so repeated calls are safe.
function liveGaugeTick() {
  const rep = window.__lastReport; if (!rep) return;
  try {
    if (rep._combined) {
      [["claude", rep.claude, false], ["codex", rep.codex, true]].forEach(([side, r, isCodex]) => {
        const root = document.getElementById("cv-" + side); if (!root || !r) return;
        paintBurnGauge({
          svg: root.querySelector(".cv-speedo"),
          zone: root.querySelector(".cv-zone2"),
          breakdown: root.querySelector(".cv-breakdown"),
          title: root.querySelector(".cvb-trow .cvb-t"),
        }, r, isCodex, localStorage.getItem("burnmeter_burn_hours_" + side) || "2");
      });
    } else {
      renderSpeedometer(rep, window.__source === "codex");
    }
  } catch (e) {}
}

function renderActiveModel(rep) {
  const body = $("active-model-body"); if (!body) return;
  let w = localStorage.getItem("burnmeter_active_window3") || "live"; if (!["live", "1", "5", "15"].includes(w)) w = "live";
  // "Canlı" = ŞU AN yazan modeller: 5dk bucket'ından canlılık eşiğiyle (<120s) filtre.
  const lam = (rep.live_active_models_by_window || {})[w === "live" ? "5" : w] || {};
  let models = (lam.models || []).filter(m => !String(m.model_id).startsWith("<"));
  if (w === "live") models = models.filter(m => (m.seconds_since_last ?? 9e9) < 120);
  if (!models.length) {
    body.innerHTML = `<div class="dim" style="padding:16px 2px">${w === "live" ? "No model running now." : `No active model in the last ${w}m.`}</div>`;
    return;
  }
  models = models.slice().sort((a, b) => (a.seconds_since_last ?? 9e9) - (b.seconds_since_last ?? 9e9));
  body.innerHTML = models.map((m, i) => {
    const s = m.seconds_since_last ?? 9e9, live = s < 120;
    return `<div class="am-row${i === 0 ? " am-top" : ""}" data-ls="${m.last_seen || ""}">
      <span class="am-dot${live ? " live" : ""}"></span>
      <span class="am-name ${modelToFamily(m.model_id)}"><span class="am-modelname">${esc(modelDisplay(m.model_id))}</span>${liveWhere(m)}</span>
      <span class="am-tpm num">${fmtInt(m.tokens_per_min || 0)}<span class="dim"> tok/min</span></span>
      <span class="am-msg dim">${m.messages || 0} msg</span>
      <span class="am-rate num">${fmtMoney(m.cost_per_hour || 0)}<span class="dim">/hr</span></span>
      <span class="am-seen${live ? " live" : ""} dim">${seenLabel(s)}</span>
    </div>`;
  }).join("");
}

// ---------- COMBINED "her ikisi" görünümü (Claude | Codex yan yana) ----------
function renderCombined(cl, cx) {
  const cv = $("combined-view"), mn = document.querySelector("main");
  if (mn) mn.style.display = "none";
  if (cv) cv.style.display = "flex";
  document.documentElement.style.setProperty("--brand", "var(--brand-claude)");
  $("app-name").textContent = "Burnmeter"; $("brand-logo").textContent = "◫";
  document.title = "Burnmeter — Claude + Codex";
  $("version").textContent = "v" + (cl._meta?.version || cx._meta?.version || "?");
  { const v = cl._meta?.version || cx._meta?.version; if (v) { _showCheckAffordance(v); checkForUpdate(v); } }
  window.__multiDevice = new Set([...Object.keys(cl.by_device || {}), ...Object.keys(cx.by_device || {})]).size > 1;
  $("data-source").textContent = `Claude ${fmtInt(cl.record_count || 0)} · Codex ${fmtInt(cx.record_count || 0)} records`;
  $("last-updated").textContent = "now";
  const a = $("cv-claude"); if (a) a.innerHTML = halfStructure(cl, false, "claude");
  const b = $("cv-codex");  if (b) b.innerHTML = halfStructure(cx, true, "codex");
  paintCombinedSide("claude", cl, false);
  paintCombinedSide("codex", cx, true);
}

// her yarıda GERÇEK burn-rate speedometer (kendi tab'larındaki gibi) + 15dk/1h/2h/4h/6h picker
function paintCombinedSide(side, rep, isCodex) {
  const root = document.getElementById("cv-" + side); if (!root) return;
  const els = () => ({ svg: root.querySelector(".cv-speedo"), zone: root.querySelector(".cv-zone2"), breakdown: root.querySelector(".cv-breakdown"), title: root.querySelector(".cvb-trow .cvb-t") });
  paintBurnGauge(els(), rep, isCodex, localStorage.getItem("burnmeter_burn_hours_" + side) || "2");
  root.querySelectorAll(".cv-picker button").forEach(b => {
    b.addEventListener("click", () => {
      localStorage.setItem("burnmeter_burn_hours_" + side, b.dataset.h);
      root.querySelectorAll(".cv-picker button").forEach(x => x.classList.toggle("active", x.dataset.h === b.dataset.h));
      paintBurnGauge(els(), rep, isCodex, b.dataset.h);
    });
  });
  // canlı aktif model: rows + 1dk/5dk/15dk picker (tek görünümdekiyle aynı pencereler)
  paintCombinedActive(side, rep);
  root.querySelectorAll(".cv-am-picker button").forEach(b => {
    b.addEventListener("click", () => {
      localStorage.setItem("burnmeter_active_window3_" + side, b.dataset.w);
      root.querySelectorAll(".cv-am-picker button").forEach(x => x.classList.toggle("active", x.dataset.w === b.dataset.w));
      paintCombinedActive(side, rep);
    });
  });
}

// İkisi yarısının "Canlı aktif model" satırları — seçili pencereye göre; tickLive saniyede bir canlılığı günceller.
function paintCombinedActive(side, rep) {
  const root = document.getElementById("cv-" + side); if (!root) return;
  const box = root.querySelector(".cv-am-rows"); if (!box) return;
  let w = localStorage.getItem("burnmeter_active_window3_" + side) || "live"; if (!["live", "1", "5", "15"].includes(w)) w = "live";
  const lam = (rep.live_active_models_by_window || {})[w === "live" ? "5" : w] || {};
  let models = (lam.models || []).filter(m => !String(m.model_id).startsWith("<"))
    .sort((a, b) => (a.seconds_since_last ?? 9e9) - (b.seconds_since_last ?? 9e9)).slice(0, 4);
  if (w === "live") models = models.filter(m => (m.seconds_since_last ?? 9e9) < 120);
  box.innerHTML = models.length ? models.map(m => {
    const s = m.seconds_since_last ?? 9e9, live = s < 120;
    return `<div class="am-row" data-ls="${m.last_seen || ""}"><span class="am-dot${live ? " live" : ""}"></span><span class="am-name ${modelToFamily(m.model_id)}"><span class="am-modelname">${esc(modelDisplay(m.model_id))}</span>${liveWhere(m)}</span><span class="am-tpm num">${fmtInt(m.tokens_per_min || 0)}<span class="dim"> tok/min</span></span><span class="am-seen${live ? " live" : ""} dim">${seenLabel(s)}</span></div>`;
  }).join("") : `<div class="dim" style="padding:6px 0">${w === "live" ? "no model running now" : `no active model in the last ${w}m`}</div>`;
}

// ===== gauge TEK KAYNAK (single tab renderSpeedometer + İkisi paintBurnGauge ortak) =====
// Hangi gauge: CODEX rate-limit verisi varsa → binding-constraint rate-limit % (duvar);
// yoksa (Claude veya limitsiz) → burn-rate $/saat. İki yer de bunu çağırır → ASLA ayrışmaz.
// LIVE windowed $/hr from the server's per-minute cost buckets, relative to the
// BROWSER'S now — so the gauge decays as time passes and reads $0 when idle,
// independent of the server cache TTL (which used to freeze it at build time →
// "stuck", and never 0 when no project ran). Falls back to the server-computed
// value for reports from an older server with no recent_costs.
function liveBurnRate(fc, hoursStr) {
  const rc = fc.recent_costs, hours = parseFloat(hoursStr) || 2;
  if (Array.isArray(rc)) {
    const cut = Date.now() / 1000 - hours * 3600;
    let cost = 0;
    for (let i = 0; i < rc.length; i++) if (rc[i][0] >= cut) cost += rc[i][1];
    return cost / hours;
  }
  return (fc.burn_rates_by_hours || {})[hoursStr] ?? fc.burn_rate_per_hour_recent ?? 0;
}

function computeGaugeSpec(rep, isCodex, hoursStr) {
  const fc = rep.forecast || {};
  const rate = liveBurnRate(fc, hoursStr);
  // Hem Claude HEM Codex → AYNI burn-rate $/saat göstergesi (tutarlılık — kullanıcı
  // talebi). Codex'in rate-limit %'si hero'da DEĞİL; yan kutuda ("Plan limiti · duvar")
  // gösterilir (renderHeroAside) → bilgi kaybolmaz, ama hero her iki araçta da burn.
  const z = rep.burn_rate_zones || { typical: 4, busy: 12, heavy: 25, max: 50 };
  const zd = !!z.insufficient_history;
  const g = {
    value: rate, max: z.max || 50, zones: z,
    ticks: zd
      ? [{ v: 0, l: "$0" }, { v: z.typical, l: "typical (est.)" }, { v: z.busy, l: "busy (est.)" }, { v: z.heavy, l: "heavy (est.)" }]
      : [{ v: 0, l: "$0" }, { v: z.typical, l: "your typical" }, { v: z.busy, l: "your heavy" }, { v: z.heavy, l: "very heavy" }],
    valueText: fmtMoney(rate),
    unitText: `/hr · last ${hoursStr === "0.0833" ? "5m" : hoursStr === "0.25" ? "15m" : hoursStr + "h"} avg (est.)`,
    zone: rate >= z.heavy ? ["bad", "🔴 heavy zone · burning very hot"]
        : rate >= z.busy ? ["warn", "🟠 busy zone"]
        : rate >= z.typical ? ["warn", "🟡 above normal"]
        :                  ["good", "🟢 calm · low burn right now"],
    ratio: rep.current_window?.vs_baseline_ratio, below: rate < z.typical,
    note: zd ? "Market-normal estimate for your plan — it recalibrates to your own pace as Burnmeter learns it."
             : "Tuned to you: “typical” is your median 5-hour block, “heavy” is your busiest ~10%. (Started from a market-normal estimate.)",
  };
  return { g, rate, fc, isLimit: false };
}

// gauge SVG çizimi (arcs + ticks + iğne + value + unit) — verilen svg elemanına.
function drawGaugeSvg(svgEl, g) {
  if (!svgEl) return;
  const cx = 200, cy = 200, r = 150, max = g.max, z = g.zones;
  const arcs =
    `<path class="speedo-zone-green"  d="${arcPath(cx,cy,r,0,z.typical,max)}" stroke-width="26" fill="none"/>` +
    `<path class="speedo-zone-yellow" d="${arcPath(cx,cy,r,z.typical,z.busy,max)}" stroke-width="26" fill="none"/>` +
    `<path class="speedo-zone-orange" d="${arcPath(cx,cy,r,z.busy,z.heavy,max)}" stroke-width="26" fill="none"/>` +
    `<path class="speedo-zone-red"    d="${arcPath(cx,cy,r,z.heavy,max,max)}" stroke-width="26" fill="none"/>`;
  const ticksSvg = g.ticks.map(t => {
    const a = 180 - (Math.min(t.v, max) / max) * 180, p = polarToCartesian(cx, cy, r + 20, a);
    const anc = a < 90 ? "start" : a > 90 ? "end" : "middle";
    return `<text class="speedo-tick-label" x="${p.x.toFixed(1)}" y="${p.y.toFixed(1)}" text-anchor="${anc}" dominant-baseline="middle">${t.l}</text>`;
  }).join("");
  const clamped = Math.max(0, Math.min(g.value, max)), na = 180 - (clamped / max) * 180;
  const tip = polarToCartesian(cx, cy, r - 6, na), tail = polarToCartesian(cx, cy, -24, na);
  const needleCol = g.zone[0] === "bad" ? "#F2555A" : g.zone[0] === "warn" ? "#F5A623" : "#2BD96B";
  svgEl.innerHTML = `${arcs}${ticksSvg}
    <line x1="${tail.x.toFixed(1)}" y1="${tail.y.toFixed(1)}" x2="${tip.x.toFixed(1)}" y2="${tip.y.toFixed(1)}" stroke="${needleCol}" stroke-width="4" stroke-linecap="round"/>
    <circle class="speedo-hub" cx="${cx}" cy="${cy}" r="8"/>
    <text class="speedo-value" x="${cx}" y="${cy + 36}">${g.valueText}</text>
    <text class="speedo-unit" x="${cx}" y="${cy + 52}">${g.unitText}</text>`;
}

// İkisi yarısının gauge'ı — single tab ile AYNI computeGaugeSpec/drawGaugeSvg → Codex'te
// de rate-limit % gauge + burn breakdown (single Codex tab'in birebir aynısı).
function paintBurnGauge(els, rep, isCodex, hoursStr) {
  if (!els.svg) return;
  const { g, fc, isLimit } = computeGaugeSpec(rep, isCodex, hoursStr);
  drawGaugeSvg(els.svg, g);
  if (els.title) els.title.textContent = isLimit ? "Limit · how far to the wall?" : "Burn rate · burning too fast?";
  if (els.zone) {
    els.zone.className = "cv-zone2 " + g.zone[0];
    let txt = g.zone[1];
    if (g.ratio != null && g.ratio > 0) txt += ` · ${g.ratio.toFixed(1)}× normal`;
    else if (g.below) txt += " · below normal";
    els.zone.textContent = txt;
  }
  if (els.breakdown) {
    const bd = (fc.burn_rates_by_hours_by_model || {})[hoursStr] || [];
    const vis = bd.filter(m => !String(m.model_id).startsWith("<")).slice(0, 4);
    els.breakdown.innerHTML = vis.length ? vis.map(m =>
      `<span class="sb-row ${modelToFamily(m.model_id)}"><span class="sb-name">${esc(modelDisplay(m.model_id))}</span><span class="sb-val">${fmtMoney(m.cost_per_hour)}/hr</span><span class="sb-share">${(m.share * 100).toFixed(0)}%</span></span>`).join("") : "";
  }
}

// bir yarının iskeleti: head + GERÇEK speedometer (svg + window picker) + canlı model + son işler
function halfStructure(rep, isCodex, side) {
  const h = localStorage.getItem("burnmeter_burn_hours_" + side) || "2";
  const picker = [["0.0833", "5m"], ["0.25", "15m"], ["1", "1h"], ["2", "2h"], ["4", "4h"], ["6", "6h"]]
    .map(([v, l]) => `<button data-h="${v}"${v === h ? ' class="active"' : ""}>${l}</button>`).join("");
  const ago = (iso) => { const s = (Date.now() - new Date(iso).getTime()) / 1000; return s < 60 ? "now" : s < 3600 ? Math.round(s / 60) + "m" : s < 86400 ? Math.round(s / 3600) + "h" : Math.round(s / 86400) + "d"; };
  let aw = localStorage.getItem("burnmeter_active_window3_" + side) || "live"; if (!["live", "1", "5", "15"].includes(aw)) aw = "live";
  const amPicker = [["live", "● Live"], ["1", "1m"], ["5", "5m"], ["15", "15m"]]
    .map(([v, l]) => `<button data-w="${v}"${v === aw ? ' class="active"' : ""}>${l}</button>`).join("");
  const recent = (rep.recent_turns || []).filter(t => !String(t.model || "").startsWith("<")).slice(0, 6);
  const recRows = recent.length ? recent.map(t => `<div class="cvr-row"><span class="cvr-when dim">${ago(t.timestamp)}</span><span class="cvr-proj"><span class="cvr-projname">${esc(t.project_label || "?")}</span>${deviceBadge(t.device)}</span><span class="cvr-model ${modelToFamily(t.model)}">${esc(modelDisplay(t.model))}</span><span class="cvr-tok num">${fmtInt(t.total_tokens || 0)}</span></div>`).join("") : `<div class="dim" style="padding:6px 0">no activity</div>`;
  return `<div class="cv-head"><span class="cv-logo">${isCodex ? "⌬" : "◔"}</span><span class="cv-title">${isCodex ? "Codex" : "Claude Code"}</span><span class="cv-sub dim">${fmtInt(rep.record_count || 0)} records</span></div>
    <div class="cvb cv-herobox">
      <div class="cvb-trow"><span class="cvb-t dim">Burn rate · burning too fast?</span><span class="picker cv-picker">${picker}</span></div>
      <svg class="cv-speedo" viewBox="0 0 400 272" preserveAspectRatio="xMidYMid meet"></svg>
      <div class="cv-zone2"></div>
      <div class="cv-breakdown speedo-breakdown"></div>
    </div>
    <div class="cvb"><div class="cvb-trow"><span class="cvb-t dim">Live active model</span><span class="picker cv-am-picker">${amPicker}</span></div><div class="cv-am-rows"></div></div>
    <div class="cvb"><div class="cvb-t dim">Recent activity</div>${recRows}</div>`;
}

// ---------- hero SPEEDOMETER (araba göstergesi) ----------
function polarToCartesian(cx, cy, r, deg) { const a = deg * Math.PI / 180; return { x: cx + r * Math.cos(a), y: cy - r * Math.sin(a) }; }
function arcPath(cx, cy, r, sv, ev, mv) {
  if (mv <= 0) return 'M 0 0';
  const a1 = 180 - (Math.min(sv, mv) / mv) * 180, a2 = 180 - (Math.min(ev, mv) / mv) * 180;
  const p1 = polarToCartesian(cx, cy, r, a1), p2 = polarToCartesian(cx, cy, r, a2);
  return `M ${p1.x.toFixed(1)} ${p1.y.toFixed(1)} A ${r} ${r} 0 0 1 ${p2.x.toFixed(1)} ${p2.y.toFixed(1)}`;
}
// "Bu araba çok mu yakıyor?" — burn rate $/sa, referans zone'lara (tipik/yoğun/
// ağır) göre iğne + verdict. Window picker (15dk/1h/2h/4h/6h) ortalama penceresi.
function renderSpeedometer(rep, isCodex) {
  const hoursStr = localStorage.getItem("burnmeter_burn_hours_" + (isCodex ? "codex" : "claude")) || "2";
  // picker'ı aktif kaynağın penceresine senkronla (kaynak değişince doğru buton yansısın)
  document.querySelectorAll("#burn-window-picker button").forEach(x => x.classList.toggle("active", x.dataset.h === hoursStr));
  // TEK KAYNAK: computeGaugeSpec — İkisi paintBurnGauge ile AYNI → single tab ↔ İkisi asla ayrışmaz.
  const { g, fc } = computeGaugeSpec(rep, isCodex, hoursStr);
  drawGaugeSvg($("speedo-svg"), g);
  const [zc, zw] = g.zone;

  $("speedo-zone").className = "speedo-zone-txt " + zc;
  const zoneEl = $("speedo-zone");
  zoneEl.textContent = zw;
  if (g.ratio != null && g.ratio > 0) {
    const span = document.createElement("span");
    span.append(" · ");
    const strong = document.createElement("strong");
    strong.textContent = g.ratio.toFixed(1) + "×";
    span.append(strong, " your normal pace");
    zoneEl.appendChild(span);
  } else if (g.below) {
    zoneEl.append(" · below normal");
  }

  // efficiency headline (fuel_efficiency) + demoted note (öbür limit / eşik kaynağı)
  const fe = rep.fuel_efficiency?.headline || {};
  const noteHtml = `<div style="font-size:12px;margin-top:7px;color:var(--text-3);line-height:1.5">${esc(g.note)}</div>`;
  // Market-normal reference numbers for the user's plan, so the "market normal" the
  // gauge mentions is actually visible as $/hr (typical/busy/heavy).
  const mz = rep.market_zones;
  const planName = mz ? ({ pro: "Pro", max5: "Max 5×", max20: "Max 20×" }[mz.plan] || mz.plan) : "";
  const marketHtml = mz
    ? `<div style="font-size:12px;margin-top:4px;color:var(--text-3);line-height:1.5">Market-normal (${planName}): <b style="color:var(--text-2)">typical ${fmtMoney(mz.typical)}</b> · busy ${fmtMoney(mz.busy)} · heavy ${fmtMoney(mz.heavy)} /hr</div>`
    : "";
  $("hero-detail").innerHTML = `<strong>${esc(fe.label || "")}</strong>${fe.detail ? " — " + esc(fe.detail) : ""}${noteHtml}${marketHtml}`;

  // per-model breakdown of this burn (which model is eating the $/hr)
  const bd = (fc.burn_rates_by_hours_by_model || {})[hoursStr] || [];
  const vis = bd.filter(m => !String(m.model_id).startsWith("<")).slice(0, 4);
  $("speedo-breakdown").innerHTML = vis.length
    ? `<div class="dim" style="font-size:10px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px">what's using the budget · est. $</div>` +
      vis.map(m => `<span class="sb-row ${modelToFamily(m.model_id)}"><span class="sb-name">${esc(modelDisplay(m.model_id))}</span>
        <span class="sb-val">${fmtMoney(m.cost_per_hour)}/hr</span><span class="sb-share">${(m.share * 100).toFixed(0)}%</span></span>`).join("")
    : "";
}

// ---------- binding-constraint aside ("duvara ne kadar var") — NET etiketli ----------
function renderHeroAside(rep, isCodex) {
  const fc = rep.forecast || {};
  const cw = rep.current_window || {};
  $("aside-title").firstChild.textContent = isCodex ? "Plan limit · wall " : "Local usage ";
  $("aside-sub").textContent = isCodex ? "Codex Pro" : "Claude";

  const bigPct = (pct, label, sub, resetSec) => {
    const c = sevClass(pct);
    return `<div style="margin-bottom:14px">
      <div style="display:flex;align-items:baseline;gap:8px">
        <span class="num ${c}" style="font-size:34px;font-weight:600">${Math.round(pct)}%</span>
        <span style="color:var(--text-3);font-size:12px;font-weight:600">${label}</span></div>
      <div class="meter-track" style="margin:8px 0 5px"><div class="meter-fill ${c}" style="width:${Math.min(100, pct)}%"></div></div>
      <div class="dim" style="font-size:11px">${sub}${resetSec ? ` · ↻ ${resetIn(resetSec)}` : ""}</div></div>`;
  };

  let body = "";
  if (isCodex) {
    // Real Codex rate limit — the actual wall. NET: "% of weekly Codex limit".
    const rl = rep.codex_rate_limits || {};
    const dev = rl.mac || Object.values(rl)[0] || {};
    const wk = dev.secondary || {}, h5 = dev.primary || {};
    body += bigPct(wk.used_percent || 0, "weekly Codex limit", "used this week", wk.resets_at);
    body += bigPct(h5.used_percent || 0, "5-hour limit", "used in the last 5 hours", h5.resets_at);
  } else {
    // Claude: Anthropic exposes NO account limit/quota → we deliberately do NOT
    // show any "% of limit" gauge (it would be fabricated). Honest local usage
    // facts only (estimated $); the user's own peaks shown as plain context.
    const bigFact = (val, label, sub) => `<div style="margin-bottom:14px">
        <div style="display:flex;align-items:baseline;gap:8px">
          <span class="num" style="font-size:32px;font-weight:600;color:var(--text-1)">${val}</span>
          <span style="color:var(--text-3);font-size:12px;font-weight:600">${label}</span></div>
        <div class="dim" style="font-size:11px;margin-top:6px">${sub}</div></div>`;
    const used = cw.cost_usd || 0, p90 = cw.baseline_cost_p90 || 0;
    const wc = rep.weekly_cap || {};
    const rate = cw.cost_per_hour;
    body += bigFact("~" + fmtMoney0(used), "this 5-hour block",
      (cw.active ? "open now" : "block closed")
      + (rate ? ` · ~${fmtMoney(rate)}/hr` : "")
      + (p90 ? ` · your heaviest block ~${fmtMoney0(p90)}` : ""));
    body += bigFact("~" + fmtMoney0(wc.rolling_7d_cost || 0), "this week (7 days)",
      wc.historical_7d_p95 ? `your heaviest week ~${fmtMoney0(wc.historical_7d_p95)}` : "local total");
    body += `<div class="dim" style="font-size:10.5px;line-height:1.45;margin:-4px 0 14px;color:var(--text-4)">Anthropic exposes no account limit/quota — these are local usage only (est. $).</div>`;
  }

  // key stats footer — clearly labeled
  const rows = [];
  rows.push(["Today", fmtMoney0(fc.today?.so_far ?? 0)]);
  if (fc.today?.projected_eod != null) rows.push(["End-of-day estimate", fmtMoney0(fc.today.projected_eod)]);
  if (!isCodex && cw.projected_close_cost != null) rows.push(["This block ~close", fmtMoney0(cw.projected_close_cost)]);
  body += `<div style="margin-top:4px;padding-top:12px;border-top:1px solid var(--border-subtle)">` +
    rows.map(([l, v]) => `<div style="display:flex;justify-content:space-between;padding:5px 0">
      <span style="color:var(--text-3);font-size:12px">${l}</span>
      <span class="num" style="color:var(--text-1);font-size:13px">${v}</span></div>`).join("") + `</div>`;

  $("hero-aside-body").innerHTML = body;
}

// ---------- KPIs ----------
function renderKPIs(rep, isCodex) {
  const fc = rep.forecast || {}, t = rep.totals || {}, ce = rep.cache_efficiency || {};
  const daily = rep.daily || [];
  const todayE = daily[daily.length - 1] || {};
  const burn = fc.burn_rate_per_hour_recent ?? 0;
  const est = isCodex ? " (est.)" : "";
  const kpis = [
    { l: "Spent today", v: "~" + fmtMoney(fc.today?.so_far ?? 0), s: `${fmtInt(todayE.messages || 0)} prompts`, spark: daily.slice(-14).map(d => d.cost_usd || 0) },
    { l: "Burn rate", v: "~" + fmtMoney(burn), unit: "/hr", s: "avg over last 2h" + est, spark: null },
    { l: "Cache savings", v: "~" + fmtMoney0(ce.usd_saved || 0), s: `~${Math.round((ce.discount_pct || 0) * 100)}% cheaper than no-cache · all-time est.`, spark: null },
    { l: "Total spent", v: "~" + fmtMoney0(t.cost_usd || 0), s: `${fmtCompact(t.total_tokens || 0)} tokens · all-time${est}`, spark: daily.slice(-14).map(d => d.cost_usd || 0) },
  ];
  kpis.forEach((k, i) => {
    const n = i + 1;
    $(`kpi${n}-l`).textContent = k.l;
    $(`kpi${n}-v`).innerHTML = k.v + (k.unit ? `<small>${k.unit}</small>` : "");
    $(`kpi${n}-s`).textContent = k.s;
    drawSpark(`kpi${n}-spark`, k.spark);
  });
}

// ---------- verimlilik · birim ekonomisi (sen vs tipik) ----------
// "Efficiency nerede?" — her birim maliyetin (saat/gün/commit/turn) senin
// referansına/endüstri tipiğine göre nerede. token harcaman verimli mi?
function renderEfficiency(rep, isCodex) {
  const fe = rep.fuel_efficiency || {};
  const vClass = (k) => k === "bad" ? "bad" : k === "warn" ? "warn" : "good";
  const row = (label, youTxt, refTxt, verdict, kind) => `
    <div style="display:grid;grid-template-columns:1fr auto 130px;gap:14px;align-items:center;padding:9px 0;border-bottom:1px solid var(--border-subtle)">
      <div><div style="font-size:13px;color:var(--text-2)">${label}</div>
        <div class="dim" style="font-size:11px;font-family:var(--font-mono);margin-top:2px">${refTxt}</div></div>
      <div class="num" style="font-size:17px;color:var(--text-1);text-align:right">${youTxt}</div>
      <div class="num" style="font-size:11px;text-align:right;font-weight:600;color:var(--${vClass(kind)})">${esc(verdict)}</div>
    </div>`;

  const ph = fe.per_active_hour || {}, pd = fe.per_active_day || {}, pc = fe.per_commit || {},
        pt = fe.per_turn || {}, ch = fe.cache_hit_rate || {};
  const est = isCodex ? " (notional)" : " (est.)";
  let html = "";
  if (ph.you != null) html += row("Cost per hour",
    "~" + fmtMoney(ph.you) + "/hr" + est, `your typical $${ph.band_normal_low}–${ph.band_normal_high}/hr range · ${ph.hours_per_active_day}h/day · active hours from 5m buckets`,
    ph.verdict_label || "", ph.verdict_kind);
  if (pd.you_avg_recent7 != null) html += row("Cost per day",
    "~" + fmtMoney0(pd.you_avg_recent7) + est, `your 30d avg ~$${pd.baseline_avg}/day (heavy P90 $${pd.baseline_p90})`,
    pd.verdict_label || "", pd.verdict_kind);
  if (pc.you_median != null) html += row("Cost per commit",
    "~" + fmtMoney(pc.you_median) + est, `${pc.matched_commits} commits matched · median/commit · no account data → no comparison`,
    pc.verdict_label || "", pc.verdict_kind);
  if (pt.you != null) html += row("Cost per turn",
    "~" + fmtMoney(pt.you) + est, `community estimate ~$${pt.baseline_low}–$${pt.baseline_high}/turn (no published norm) · ${fmtInt(pt.turns)} turns · all-time avg`,
    pt.verdict_label || "", pt.verdict_kind);
  if (ch.you != null) html += row("Cache hit",
    fmtPct(ch.you), `target ≥${Math.round((ch.target_excellent || .85) * 100)}% excellent`,
    ch.verdict_label || "", ch.verdict_kind);

  $("eff-body").innerHTML = html || `<div class="empty">Not enough data</div>`;
}

// ---------- cache efficiency ----------
function renderCache(rep) {
  const ce = rep.cache_efficiency || {};
  const hit = (ce.hit_rate || 0) * 100;
  $("cache-body").innerHTML =
    `<div style="display:flex;align-items:baseline;gap:8px;margin-bottom:4px">
       <span class="num" style="font-size:34px;font-weight:600;color:var(--text-1)">${hit.toFixed(1)}%</span>
       <span class="dim" style="font-size:12px">cache hit</span></div>
     <div class="meter-track" style="margin:10px 0"><div class="meter-fill good" style="width:${Math.min(100, hit)}%"></div></div>
     <div style="display:flex;justify-content:space-between;margin-top:14px">
       <div><div class="num" style="font-size:18px;color:var(--good)">~${fmtMoney0(ce.usd_saved || 0)}</div><div class="dim" style="font-size:11px">saved by caching (est.)</div></div>
       <div style="text-align:right"><div class="num" style="font-size:18px;color:var(--text-1)">~${fmtMoney0(ce.usd_on_cache || 0)}</div><div class="dim" style="font-size:11px">you paid for cache (est.)</div></div>
     </div>
     <div class="dim" style="font-size:11px;margin-top:12px;line-height:1.5">Caching cut your input cost ~${Math.round((ce.discount_pct || 0) * 100)}%. The same work without it would have cost about ~${fmtMoney0(ce.full_equiv_usd || 0)} (rough estimate).</div>`;
}

// ---------- aylık BÜTÇE takibi (#7) ----------
// Per-source aylık $ bütçe (localStorage burnmeter_budget_{source}). Bu ay harcanan vs
// bütçe + ay-sonu projeksiyonu (forecast.month). $ TAHMİNİDİR — başlıkta + notta etiketli.
// Bütçe sadece bu tarayıcıda saklanır (sunucuya gitmez).
const _budgetEditing = {};
function renderBudget(rep, isCodex) {
  const body = $("budget-body");
  if (!body) return;
  const src = isCodex ? "codex" : "claude";
  const m = (rep.forecast || {}).month || {};
  const soFar = m.so_far || 0;
  const projEom = m.projected_eom || 0;
  const daysLeft = m.days_left ?? 0;
  const budget = parseFloat(localStorage.getItem("burnmeter_budget_" + src) || "0") || 0;
  const dailyRate = daysLeft > 0 ? Math.max(0, projEom - soFar) / daysLeft : 0;

  // bütçe yok VEYA düzenleniyor → input formu
  if (budget <= 0 || _budgetEditing[src]) {
    body.innerHTML =
      `<div class="budget-edit-wrap">
         <div class="dim" style="font-size:12.5px;line-height:1.55;max-width:560px">
           ${budget > 0 ? "Update your monthly budget." : "Set a monthly $ budget — see what you've spent this month and the end-of-month estimate against it."}
           <span style="color:var(--text-4)">Stored only in this browser; figures are estimates.</span>
         </div>
         <div class="budget-set">
           <span class="budget-cur">$</span>
           <input id="budget-input" type="number" min="0" step="10" placeholder="200" value="${budget > 0 ? budget : ""}" inputmode="decimal" />
           <button id="budget-save">Save</button>
           ${budget > 0 ? `<button id="budget-cancel" class="budget-ghost">cancel</button><button id="budget-clear" class="budget-ghost">clear</button>` : ""}
         </div>
         <div class="dim" style="font-size:11px">${src} · so far this month: ~${fmtMoney0(soFar)}</div>
       </div>`;
    const done = () => { _budgetEditing[src] = false; if (window.__lastReport) renderBudget(window.__lastReport, isCodex); };
    const inp = $("budget-input");
    const saveBtn = $("budget-save");
    if (saveBtn) saveBtn.addEventListener("click", () => {
      const v = parseFloat(inp.value);
      if (v > 0) localStorage.setItem("burnmeter_budget_" + src, String(v));
      done();
    });
    if (inp) inp.addEventListener("keydown", e => {
      if (e.key === "Enter") saveBtn.click();
      else if (e.key === "Escape" && budget > 0) done();
    });
    const clearBtn = $("budget-clear");
    if (clearBtn) clearBtn.addEventListener("click", () => { localStorage.removeItem("burnmeter_budget_" + src); done(); });
    const cancelBtn = $("budget-cancel");
    if (cancelBtn) cancelBtn.addEventListener("click", done);
    // Focus only when the user actively opened the editor (clicked "düzenle").
    // Focusing on a passive re-render — e.g. an unset budget after a tab switch —
    // makes the browser scroll the input into view, yanking the page down to the
    // budget card. Gate on _budgetEditing so tab switches no longer jump-scroll.
    if (inp && _budgetEditing[src]) inp.focus();
    return;
  }

  const pct = (soFar / budget) * 100;
  const projPct = (projEom / budget) * 100;
  const over = projEom - budget;
  const cls = projEom > budget ? "bad" : pct > 90 ? "warn" : "good";
  const verdict = projEom > budget
    ? `<span style="color:var(--bad);font-weight:600">⚠ ~${fmtMoney0(over)} overage projected</span>`
    : `<span style="color:var(--good);font-weight:600">✓ within budget — ~${fmtMoney0(budget - projEom)} headroom left</span>`;
  const projMark = (projPct > pct && projPct <= 100)
    ? `<span class="budget-proj" style="left:${projPct.toFixed(1)}%" title="end-of-month estimate ~${fmtMoney0(projEom)}"></span>` : "";

  body.innerHTML =
    `<div class="budget-grid">
       <div class="budget-nums">
         <div><span class="num" style="font-size:30px;font-weight:600;color:var(--text-1)">~${fmtMoney0(soFar)}</span>
           <span class="dim" style="font-size:13px"> / ${fmtMoney0(budget)}</span></div>
         <div class="dim" style="font-size:11px;margin-top:2px">spent this month · ${pct.toFixed(0)}%</div>
       </div>
       <div class="budget-barwrap">
         <div class="meter-track" style="height:12px;position:relative;overflow:visible">
           <div class="meter-fill ${cls}" style="width:${Math.min(100, pct).toFixed(1)}%"></div>
           ${projMark}
         </div>
         <div style="display:flex;justify-content:space-between;font-size:11px;margin-top:7px" class="dim">
           <span>$0</span>
           <span>end-of-month est: ~${fmtMoney0(projEom)} (${projPct.toFixed(0)}%)</span>
           <span>${fmtMoney0(budget)}</span>
         </div>
       </div>
       <div class="budget-verdict">
         ${verdict}
         <div class="dim" style="font-size:11px;margin-top:4px">${daysLeft}d left · typical ~${fmtMoney(dailyRate)}/day · $ is an estimate</div>
         <button id="budget-edit" class="budget-ghost" style="margin-top:9px">✎ edit budget</button>
       </div>
     </div>`;
  const editBtn = $("budget-edit");
  if (editBtn) editBtn.addEventListener("click", () => { _budgetEditing[src] = true; renderBudget(rep, isCodex); });
}

// ---------- Pro cross-device sync — senkron cihazların özetleri (E2E) ----------
// Server /api/sync/devices'i (sunucu deşifre eder, lokal UI'a verir; relay yalnızca
// ciphertext görür) çekip cihaz listesini çizer. Sync kapalıysa "aç" daveti gösterir.
async function renderSyncDevices() {
  const body = $("sync-devices-body");
  if (!body) return;
  let data;
  try {
    const r = await fetch("/api/sync/devices");
    data = await r.json();
  } catch (e) { return; }   // server eski olabilir → sessizce geç
  if (!data.configured) {
    body.innerHTML =
      `<div style="display:flex;align-items:center;gap:20px;flex-wrap:wrap;max-width:820px">
         <div style="flex:1;min-width:280px">
           <div style="font-size:14.5px;font-weight:600;color:var(--text-1);margin-bottom:5px">All your machines in one view <span style="color:var(--brand)">✦ Pro</span></div>
           <div class="dim" style="font-size:12.5px;line-height:1.6">Sync your Claude Code + Codex usage across every device — end-to-end encrypted, so only summaries sync and your raw logs never leave your machine.</div>
         </div>
         <a href="https://burnmeter.dev/#pricing" target="_blank" rel="noopener" class="get-pro-btn">Get Pro →</a>
       </div>`;
    return;
  }
  if (data.error) {
    body.innerHTML = `<div class="dim" style="font-size:12px;color:var(--warn)">couldn't reach relay: ${esc(String(data.error))}</div>`;
    return;
  }
  const devs = data.devices || [];
  if (!devs.length) {
    body.innerHTML = `<div class="dim" style="font-size:12.5px">no synced devices yet — push this device with <span style="font-family:var(--font-mono)">burnmeter sync push</span>.</div>`;
    return;
  }
  const ago = (iso) => { if (!iso) return ""; const s = (Date.now() - new Date(iso).getTime()) / 1000; return s < 60 ? "now" : s < 3600 ? Math.round(s / 60) + "m" : s < 86400 ? Math.round(s / 3600) + "h" : Math.round(s / 86400) + "d"; };
  body.innerHTML = `<div class="sync-grid">` + devs.map(d => {
    if (d._undecryptable) {
      return `<div class="sync-dev"><div class="sync-dev-head"><span class="sync-dot bad"></span><b>${esc(d.device_id || "?")}</b></div>
        <div class="dim" style="font-size:11px">couldn't decrypt · passphrase may differ</div></div>`;
    }
    const me = d.device_id === data.this_device;
    const srcs = d.sources || {};
    const rows = Object.keys(srcs).map(s => {
      const v = srcs[s] || {};
      return `<div class="sync-src"><span class="sync-srcname ${s === 'codex' ? 'fam-gpt' : 'fam-opus'}">${esc(s)}</span>
        <span class="num">~${fmtMoney(v.month_so_far || 0)}</span><span class="dim">this month</span>
        <span class="num">${fmtInt(v.record_count || 0)}</span><span class="dim">records</span></div>`;
    }).join("") || `<div class="dim" style="font-size:11px">no data</div>`;
    // cross-device recent activity — straight from each device's snapshot tail (metadata only, no raw logs)
    const recent = Object.keys(srcs)
      .flatMap(s => (srcs[s].recent || []).map(t => ({ ...t, _src: s })))
      .sort((a, b) => new Date(b.ts || 0) - new Date(a.ts || 0)).slice(0, 6);
    const recentHtml = recent.length ? `<div class="sync-recent">` + recent.map(t =>
      `<div class="sync-rrow"><span class="sync-rago dim">${ago(t.ts)}</span>` +
      `<span class="sync-rproj">${esc(t.project || "?")}</span>` +
      `<span class="sync-rmodel ${t._src === 'codex' ? 'fam-gpt' : 'fam-opus'}">${esc(modelDisplay(t.model))}</span>` +
      `<span class="sync-rtok num">${fmtInt(t.tokens || 0)}</span></div>`
    ).join("") + `</div>` : "";
    return `<div class="sync-dev${me ? ' me' : ''}">
      <div class="sync-dev-head"><span class="sync-dot live"></span><b>${esc(d.label || "?")}</b>${me ? '<span class="sync-badge">this device</span>' : ''}<span class="sync-ago dim">${ago(d._updated_at)}</span></div>
      ${rows}${recentHtml}</div>`;
  }).join("") + `</div>`;
}

// ---------- trend chart ----------
const MA_COLORS = { 7: "#8b97ff", 25: "#3fd9e8", 50: "#ffc05a", 100: "#ff8a8e" };
function movingAverage(arr, n) {
  return arr.map((_, i) => {
    const w = arr.slice(Math.max(0, i - n + 1), i + 1);
    return w.reduce((a, b) => a + b, 0) / w.length;
  });
}
// Trade-chart: $/saat bar + seçili hareketli ortalama (MA) çizgileri.
function renderTrend(rep, isCodex) {
  const tf = localStorage.getItem("burnmeter_trend_tf") || "24";
  const maList = JSON.parse(localStorage.getItem("burnmeter_trend_ma") || "[7]");
  $("trend-unit").textContent = "$/hr (est.) · " + (tf === "24" ? "1 day" : tf + "h") + " window";
  const series = (rep.burn_rate_timeseries || {})[tf] || [];
  const shortWin = (tf === "1" || tf === "4" || tf === "6");
  const labels = series.map(p => {
    const dt = p.start ? new Date(p.start) : null;
    if (!dt) return "";
    return shortWin ? dt.toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit" })
                    : (dt.getMonth() + 1) + "/" + dt.getDate();
  });
  const burns = series.map(p => p.cost_per_hour ?? p.cost ?? 0);
  const datasets = [{ type: "bar", label: `$/hr`, data: burns,
    backgroundColor: "rgba(94,106,210,.32)", borderColor: "transparent", borderRadius: 2, order: 10 }];
  for (const n of maList) {
    datasets.push({ type: "line", label: `MA ${n}`, data: movingAverage(burns, n),
      borderColor: MA_COLORS[n] || "#fff", borderWidth: 2, fill: false, tension: .3,
      pointRadius: 0, pointHoverRadius: 3, order: 1 });
  }
  const ctx = $("chart-trend");
  if (window.__charts.trend) window.__charts.trend.destroy();
  window.__charts.trend = new Chart(ctx, {
    type: "bar", data: { labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      plugins: {
        legend: { display: maList.length > 0, position: "top", align: "end",
          labels: { color: "#8A8F98", font: { size: 10 }, boxWidth: 10, padding: 8, filter: i => i.text !== "$/hr" } },
        tooltip: { ...tooltipCfg(), callbacks: { label: c => `${c.dataset.label}: ~${fmtMoney(c.parsed.y || 0)}/hr (est.)` } }
      },
      scales: {
        x: { grid: { display: false }, border: { display: false }, ticks: { color: "#62666D", font: { size: 10, family: "'Geist Mono',monospace" }, maxTicksLimit: 8 } },
        y: { grid: { color: "rgba(255,255,255,0.05)" }, border: { display: false }, ticks: { color: "#62666D", font: { size: 10, family: "'Geist Mono',monospace" }, maxTicksLimit: 5, callback: v => "$" + v } }
      }
    }
  });
}

// ---------- model donut ----------
function renderModels(rep) {
  const bmf = (rep.by_model_full || []).filter(m => !String(m.model_id || "").startsWith("<")).slice(0, 8);
  const labels = bmf.map(m => modelDisplay(m.model_id));
  const data = bmf.map(m => m.cost_usd || 0);
  const total = data.reduce((a, b) => a + b, 0);
  const palette = ["#5E6AD2", "#02B8CC", "#2BD96B", "#E0A3FF", "#F5A623", "#F2555A", "#7C8CF0", "#5BD6E0"];
  const ctx = $("chart-models");
  if (window.__charts.models) window.__charts.models.destroy();
  window.__charts.models = new Chart(ctx, {
    type: "doughnut",
    data: { labels, datasets: [{ data, backgroundColor: palette, borderColor: "#0F1011", borderWidth: 2 }] },
    options: { responsive: true, maintainAspectRatio: false, animation: false, cutout: "66%", plugins: { legend: { display: false }, tooltip: tooltipCfg() } }
  });
  $("models-total").textContent = fmtMoney0(total);
  $("models-legend").innerHTML = bmf.map((m, i) =>
    `<div class="bar-list-row"><span style="width:9px;height:9px;border-radius:2px;background:${palette[i % palette.length]};flex-shrink:0"></span>
      <span class="nm" style="width:auto;flex:1;color:var(--text-2)">${esc(modelDisplay(m.model_id))}</span>
      <span class="vl">${fmtMoney0(m.cost_usd)}</span></div>`).join("");
}

// ---------- daily bars ----------
function renderDaily(rep) {
  const daily = (rep.daily || []).slice(-30);
  const labels = daily.map(d => (d.date || "").slice(5));
  const data = daily.map(d => d.cost_usd || 0);
  const today = labels.length - 1;
  const colors = data.map((_, i) => i === today ? "#5E6AD2" : "rgba(98,102,109,0.5)");
  $("daily-sub").textContent = daily.length ? `total ~${fmtMoney0(data.reduce((a, b) => a + b, 0))} (est.)` : "";
  const ctx = $("chart-daily");
  if (window.__charts.daily) window.__charts.daily.destroy();
  // Daily bars are per-DAY totals (estimated $), not an hourly rate — override
  // the shared yMoney tooltip (which appends "/sa") to label them correctly.
  const dailyOpts = chartOpts({ yMoney: true });
  dailyOpts.plugins.tooltip.callbacks = { label: (c) => fmtMoney(c.parsed.y) + " (daily total · est.)" };
  window.__charts.daily = new Chart(ctx, {
    type: "bar",
    data: { labels, datasets: [{ data, backgroundColor: colors, borderRadius: 3, maxBarThickness: 18 }] },
    options: dailyOpts
  });
}

// ---------- projects ----------
function renderProjects(rep) {
  const ps = (rep.by_project || []).slice(0, 12);
  const max = Math.max(...ps.map(p => p.cost_usd || 0), 1);
  const tb = document.querySelector("#projects-tbl tbody");
  if (!ps.length) { tb.innerHTML = `<tr><td colspan="3" class="empty">No data</td></tr>`; return; }
  tb.innerHTML = ps.map(p =>
    `<tr><td><div>${esc(p.project_label)}</div><div class="cell-bar"><i style="width:${(p.cost_usd || 0) / max * 100}%"></i></div></td>
      <td class="num">${fmtCompact(p.total_tokens || 0)}</td>
      <td class="num">${fmtMoney0(p.cost_usd)}</td></tr>`).join("");
}

// ---------- recent turns ----------
function renderRecent(rep) {
  const isCodex = (rep._meta?.source || "claude") === "codex";
  const rsub = $("recent-sub");
  if (rsub) rsub.textContent = isCodex ? "tokens that count toward your limit" : "tokens, weighted by cost";
  const turns = (rep.recent_turns || []).filter(t => !String(t.model || "").startsWith("<"));
  const tb = document.querySelector("#recent-tbl tbody");
  if (!turns.length) { tb.innerHTML = `<tr><td colspan="6" class="empty">No activity yet</td></tr>`; return; }
  tb.innerHTML = turns.map(t => {
    const fam = modelToFamily(t.model);
    return `<tr>
      <td>${relTime(t.timestamp)}</td>
      <td><div>${esc(t.project_label)} ${deviceBadge(t.device)}</div></td>
      <td><span class="pill ${fam}">${modelDisplay(t.model)}</span></td>
      <td class="num">${fmtInt(t.effective_tokens)}</td>
      <td class="num">${fmtMoney(t.cost_usd)}</td>
      <td class="num">${fmtPct(t.cache_hit_rate)}</td></tr>`;
  }).join("");
}

// ---------- aktivite ısı haritası (saat × gün · yerel) ----------
function renderHeatmap(rep) {
  const body = $("heatmap-body"); if (!body) return;
  const hm = rep.usage_heatmap || {}, grid = hm.grid || [], mx = hm.max || 0;
  if (!grid.length || !mx) { body.innerHTML = `<div class="dim" style="padding:10px 0">no activity data</div>`; return; }
  const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  let h = `<div class="hm-grid"><div class="hm-corner"></div>`;
  for (let x = 0; x < 24; x++) h += `<div class="hm-hour">${x % 3 === 0 ? x : ""}</div>`;
  for (let d = 0; d < 7; d++) {
    h += `<div class="hm-day">${days[d]}</div>`;
    for (let x = 0; x < 24; x++) {
      const v = grid[d][x] || 0, it = mx ? v / mx : 0;
      const lvl = v === 0 ? 0 : it > 0.66 ? 4 : it > 0.33 ? 3 : it > 0.12 ? 2 : 1;
      h += `<div class="hm-cell lvl${lvl}" title="${days[d]} ${x}:00 · ${fmtInt(v)} tokens"></div>`;
    }
  }
  body.innerHTML = h + `</div>`;
}

// ---------- per-model table ----------
function renderModelTable(rep) {
  const bmf = (rep.by_model_full || []).filter(m => !String(m.model_id || "").startsWith("<")).slice(0, 10);
  const tb = document.querySelector("#modeltbl tbody");
  if (!bmf.length) { tb.innerHTML = `<tr><td colspan="4" class="empty">No data</td></tr>`; return; }
  tb.innerHTML = bmf.map(m => {
    const fam = modelToFamily(m.model_id);
    return `<tr><td><span class="pill ${fam}">${modelDisplay(m.model_id)}</span></td>
      <td class="num">${fmtInt(m.messages)}</td>
      <td class="num">${fmtCompact(m.total_tokens)}</td>
      <td class="num">${fmtMoney0(m.cost_usd)}</td></tr>`;
  }).join("");
}

// ---------- behavior (errors) ----------
const ERR_LABELS = { timeout: "timeout", file_not_found: "file not found", permission_denied: "permission denied", command_not_found: "command not found", python_error: "python", bash_failure: "bash exit1", stale_reference: "stale ref", output_too_large: "large output", rate_limited: "rate limit", other_error: "other" };
function renderBehavior(rep) {
  const e = rep.errors || {};
  $("behavior-sub").textContent = `${e.total || 0} errors · lifetime ${fmtInt(e.total_lifetime || 0)}`;
  const cats = (e.by_category || []).slice(0, 5);
  if (!cats.length) { $("behavior-body").innerHTML = `<div style="display:flex;align-items:center;gap:8px;padding:10px 0"><span class="num" style="font-size:30px;color:var(--good)">0</span><span class="dim">clean in last 24h</span></div>`; return; }
  const max = Math.max(...cats.map(c => c.count), 1);
  $("behavior-body").innerHTML =
    `<div style="display:flex;align-items:baseline;gap:8px;margin-bottom:10px"><span class="num" style="font-size:28px;color:${e.total > 20 ? "var(--warn)" : "var(--text-1)"}">${e.total || 0}</span><span class="dim" style="font-size:12px">last 24h</span></div>` +
    cats.map(c => {
      const col = c.share > 0.5 ? "var(--bad)" : c.share > 0.2 ? "var(--warn)" : "var(--accent)";
      return `<div class="bar-list-row"><span class="nm">${ERR_LABELS[c.category] || esc(c.category)}</span>
        <span class="tr"><i style="width:${c.count / max * 100}%;background:${col}"></i></span>
        <span class="vl">${c.count}</span></div>`;
    }).join("");
}

// ---------- tools ----------
function renderTools(rep) {
  const all = (rep.by_tool || []).filter(t => t.tool && t.tool !== "(no tool)");
  if (!all.length) { $("tools-sub").textContent = ""; $("tools-body").innerHTML = `<div class="empty">No tool data (Codex tool names aren't parsed)</div>`; return; }
  const rows = all.map(t => ({ tool: t.tool, calls: t.calls || 0, tok: t.approx_output_tokens || 0 })).sort((a, b) => b.tok - a.tok).slice(0, 8);
  const totalTok = all.reduce((s, t) => s + (t.approx_output_tokens || 0), 0);
  const mcp = all.filter(t => /^mcp__/.test(t.tool) || /Chrome|chrome/.test(t.tool));
  const ctok = mcp.reduce((s, t) => s + (t.approx_output_tokens || 0), 0);
  $("tools-sub").textContent = `≈${fmtCompact(totalTok)} output tokens` + (ctok ? ` · MCP ${(ctok / totalTok * 100).toFixed(1)}% (all)` : "");
  const max = Math.max(...rows.map(r => r.tok), 1);
  $("tools-body").innerHTML = rows.map(r => {
    const short = r.tool.replace(/^mcp__/, "").replace(/__/g, ":").slice(0, 28);
    return `<div class="bar-list-row"><span class="nm" title="${esc(r.tool)}">${esc(short)}</span>
      <span class="tr"><i style="width:${r.tok / max * 100}%"></i></span>
      <span class="vl" title="approximated by dividing output tokens equally across tool calls">${fmtInt(r.calls)}× · ≈${fmtCompact(r.tok)}t</span></div>`;
  }).join("");
}

// ---------- charts shared ----------
function tooltipCfg() {
  return { backgroundColor: "#1C1D1F", borderColor: "rgba(255,255,255,0.09)", borderWidth: 1, titleColor: "#F7F8F8", bodyColor: "#D0D6E0", padding: 10, cornerRadius: 8, displayColors: false, bodyFont: { family: "'Geist Mono',monospace" } };
}
function chartOpts({ yMoney }) {
  return {
    responsive: true, maintainAspectRatio: false, animation: false,
    plugins: { legend: { display: false }, tooltip: { ...tooltipCfg(), callbacks: yMoney ? { label: (c) => fmtMoney(c.parsed.y) + "/hr" } : {} } },
    scales: {
      x: { grid: { display: false }, border: { display: false }, ticks: { color: "#62666D", font: { size: 10, family: "'Geist Mono',monospace" }, maxTicksLimit: 8 } },
      y: { grid: { color: "rgba(255,255,255,0.05)" }, border: { display: false }, ticks: { color: "#62666D", font: { size: 10, family: "'Geist Mono',monospace" }, maxTicksLimit: 5, callback: (v) => yMoney ? "$" + v : v } }
    }
  };
}
function drawSpark(canvasId, data) {
  const ctx = $(canvasId);
  if (!ctx) return;
  if (window.__charts[canvasId]) window.__charts[canvasId].destroy();
  if (!data || !data.length) { ctx.style.display = "none"; return; }
  ctx.style.display = "";
  window.__charts[canvasId] = new Chart(ctx, {
    type: "line",
    data: { labels: data.map((_, i) => i), datasets: [{ data, borderColor: "#5E6AD2", borderWidth: 1.5, fill: false, tension: 0.4, pointRadius: 0 }] },
    options: { responsive: true, maintainAspectRatio: false, animation: false, plugins: { legend: { display: false }, tooltip: { enabled: false } }, scales: { x: { display: false }, y: { display: false } } }
  });
}

// ---------- live stamp ----------
function updateLiveStamp() {
  const last = window.__lastRefreshMs; if (!last) return;
  const s = Math.floor((Date.now() - last) / 1000);
  const dot = $("live-dot");
  $("last-updated").textContent = s < 2 ? "now" : s < 60 ? s + "s ago" : Math.floor(s / 60) + "m ago";
  if (dot) { dot.classList.toggle("live", s < 30); dot.classList.toggle("stale", s >= 30); }
}

// ---------- init ----------
// ---------- modular grid (GridStack: sürükle · resize · yer değiştir · kaydet) ----------
function initModularGrid() {
  if (typeof GridStack === "undefined") return;   // CDN yüklenmediyse statik layout'a düş
  const main = document.querySelector("main");
  if (!main || main.classList.contains("gs-active")) return;
  const cards = [...main.querySelectorAll(":scope > .card")];
  if (!cards.length) return;
  const colW = c => { const m = (c.className.match(/col-(\d+)/) || [])[1]; return m ? +m : 4; };
  // varsayılan yükseklikler (satır = 76px); kullanıcı resize edip kaydedebilir
  // Row heights tuned so each card FITS its content (no card scrollbars) and tall
  // cards don't float in empty space. Cards with an inner .scroll table (recent,
  // projects, model-table) stay tall; charts get the room they need.
  const H = { hero: 6, "hero-aside": 6, kpi: 2, eff: 6, cache: 4, trend: 6, models: 5,
              "active-model": 2, daily: 5, projects: 5, recent: 5, heatmap: 3, "model-table": 5, behavior: 4, tools: 4,
              budget: 3, "sync-devices": 2 };
  const gs = document.createElement("div");
  gs.className = "grid-stack";
  cards.forEach(card => {
    // The welcome banner is NOT a grid widget — it stays a plain full-width banner
    // ABOVE the grid (a direct child of <main>). As a GridStack item it would
    // reserve a 4-row slot even while display:none'd → an empty band at the top.
    if (card.dataset.card === "welcome") return;
    const item = document.createElement("div");
    item.className = "grid-stack-item";
    item.setAttribute("gs-w", colW(card));
    item.setAttribute("gs-h", H[card.dataset.card] || 4);
    item.setAttribute("gs-id", card.dataset.card || ("c" + Math.random().toString(36).slice(2, 8)));
    const content = document.createElement("div");
    content.className = "grid-stack-item-content";
    content.appendChild(card);
    item.appendChild(content);
    gs.appendChild(item);
  });
  main.appendChild(gs);
  main.classList.add("gs-active");
  const grid = GridStack.init({
    column: 12, cellHeight: 76, margin: 8, float: false, animate: false,
    // (A) ekran çözünürlüğüne göre otomatik kolon sayısı: dar ekranda yığ, geniş ekranda yay
    //   >1100px → 12 kolon (2K/4K/laptop tam grid) · 700-1100 → 6 kolon · <700 → 1 kolon (stack)
    columnOpts: { breakpointForWindow: true, breakpoints: [{ w: 700, c: 1 }, { w: 1100, c: 6 }] },
    // interaktif öğelerden sürükleme başlatma (tıklamalar çalışsın)
    draggable: { cancel: "button, input, select, a, canvas, table, .picker, .indicator-bar, .scroll, .tbl, .speedo-breakdown, .donut-wrap" },
    resizable: { handles: "e, se, s, sw, w" },
  }, gs);
  window.__grid = grid;
  try {
    const saved = JSON.parse(localStorage.getItem("burnmeter_layout_v4") || "null");
    // kaydet/yükle YALNIZCA tam grid'de (12 kolon). Dar ekranda GridStack otomatik reflow
    // yapar; o geçici dar düzeni kaydetmeyiz ki geniş ekran düzenini bozmasın.
    if (saved && saved.length && grid.getColumn() === 12) grid.load(saved, false);
  } catch (e) { /* bozuk kayıt → varsayılan */ }
  const save = () => { try { if (grid.getColumn() === 12) localStorage.setItem("burnmeter_layout_v4", JSON.stringify(grid.save(false))); } catch (e) {} };
  grid.on("change", save);
  // resize sonrası grafikleri yeni boyuta sığdır
  grid.on("resizestop", () => { Object.values(window.__charts || {}).forEach(ch => { try { ch.resize(); } catch (e) {} }); });
}

// ---------- pre-limit ALERTS (free: tarayıcı bildirimi + in-page toast; Pro: desktop/Slack push) ----------
function alertLevelOf(rep, isCodex) {
  // binding-constraint tehlike: 0 güvende · 2 uyarı · 3 kritik
  if (isCodex && rep.codex_rate_limits && Object.keys(rep.codex_rate_limits).length) {
    const dev = rep.codex_rate_limits.mac || Object.values(rep.codex_rate_limits)[0] || {};
    const bp = Math.max(dev.primary?.used_percent || 0, dev.secondary?.used_percent || 0);
    if (bp >= 95) return { level: 3, msg: `🔴 Codex limit ${Math.round(bp)}% — throttle VERY CLOSE` };
    if (bp >= 85) return { level: 2, msg: `🟠 Codex limit ${Math.round(bp)}% — approaching the wall` };
    return { level: 0, msg: "" };
  }
  // Claude (hesap limiti yok) → aşırı burn uyarısı
  const fc = rep.forecast || {}, z = rep.burn_rate_zones || {};
  const rate = (fc.burn_rates_by_hours || {})["2"] ?? fc.burn_rate_per_hour_recent ?? 0;
  if (z.heavy && rate >= z.heavy * 1.6) return { level: 2, msg: `🟠 Burn rate very high: ${fmtMoney(rate)}/hr` };
  return { level: 0, msg: "" };
}
function checkAlerts(rep, isCodex) {
  if (localStorage.getItem("burnmeter_alerts_on") !== "1") return;
  const key = "burnmeter_alert_lvl_" + (isCodex ? "codex" : "claude");
  const { level, msg } = alertLevelOf(rep, isCodex);
  const prev = parseInt(localStorage.getItem(key) || "0", 10);
  localStorage.setItem(key, String(level));
  if (level >= 2 && level > prev) fireAlert(msg, level);   // SADECE eşik aşıldığında (de-dup)
}
function fireAlert(msg, level) {
  let t = $("alert-toast");
  if (!t) { t = document.createElement("div"); t.id = "alert-toast"; document.body.appendChild(t); }
  t.className = "alert-toast " + (level >= 3 ? "crit" : "warn") + " show";
  t.textContent = msg;
  clearTimeout(window.__alertHide);
  window.__alertHide = setTimeout(() => t.classList.remove("show"), 12000);
  try { if (window.Notification && Notification.permission === "granted") new Notification("Burnmeter · limit alert", { body: msg }); } catch (e) {}
}
function wireAlerts() {
  const b = $("alert-toggle"); if (!b) return;
  const sync = () => { const on = localStorage.getItem("burnmeter_alerts_on") === "1"; b.classList.toggle("on", on); b.title = on ? "limit alerts ON" : "limit alerts off (click)"; };
  sync();
  b.addEventListener("click", async () => {
    const on = localStorage.getItem("burnmeter_alerts_on") === "1";
    if (!on) {
      localStorage.setItem("burnmeter_alerts_on", "1");
      try { if (window.Notification && Notification.permission === "default") await Notification.requestPermission(); } catch (e) {}
      fireAlert("🔔 Limit alerts on — I'll warn you at 85% of the wall.", 2);
    } else { localStorage.setItem("burnmeter_alerts_on", "0"); }
    sync();
  });
}

// Scale the whole UI up on physically large screens. We key off PHYSICAL pixels
// (CSS viewport × devicePixelRatio), not CSS width, so a 2K/4K monitor running
// Windows display scaling (e.g. 150% → ~1707 CSS px) still zooms — the CSS-width
// media-query tiers alone missed exactly that case (the "2K too small" report).
function applyZoom() {
  const phys = (window.innerWidth || 0) * (window.devicePixelRatio || 1);
  const z = phys >= 3400 ? 1.55 : phys >= 2400 ? 1.30 : phys >= 2000 ? 1.12 : 1;
  try { document.body.style.zoom = z; } catch (e) {}
}

function start() {
  applyZoom();
  window.addEventListener("resize", applyZoom);
  $("refresh").addEventListener("click", () => refresh(true));
  wireAlerts();
  $("export-csv")?.addEventListener("click", () => {
    const s = window.__source === "both" ? "claude" : (window.__source || "claude");
    window.location.href = "/api/records.csv" + (s !== "claude" ? "?source=" + s : "");
  });
  // source toggle
  document.querySelectorAll("#source-toggle button").forEach(b => {
    b.classList.toggle("active", b.dataset.source === window.__source);
    b.addEventListener("click", () => {
      const src = b.dataset.source;
      if (src === window.__source) return;
      window.__source = src; localStorage.setItem("burnmeter_source", src);
      document.querySelectorAll("#source-toggle button").forEach(x => x.classList.toggle("active", x.dataset.source === src));
      const instant = renderFromCache(src);                 // INSTANT switch if this source is already cached
      if (!instant) $("last-updated").textContent = "Burnmeter loading…";
      window.__refreshInFlight = false; refresh(false);     // silent background revalidate (SWR)
    });
  });
  // burn-window picker (speedometer ortalama penceresi: 15dk/1h/2h/4h/6h)
  const bh0 = localStorage.getItem("burnmeter_burn_hours_" + window.__source) || "2";
  document.querySelectorAll("#burn-window-picker button").forEach(b => {
    b.classList.toggle("active", b.dataset.h === bh0);
    b.addEventListener("click", () => {
      localStorage.setItem("burnmeter_burn_hours_" + window.__source, b.dataset.h);
      document.querySelectorAll("#burn-window-picker button").forEach(x => x.classList.toggle("active", x.dataset.h === b.dataset.h));
      if (window.__lastReport) renderSpeedometer(window.__lastReport, window.__source === "codex");
    });
  });
  // trend timeframe picker (1h/4h/6h/12h/1d)
  const tf0 = localStorage.getItem("burnmeter_trend_tf") || "24";
  document.querySelectorAll("#trend-picker button").forEach(b => {
    b.classList.toggle("active", b.dataset.tf === tf0);
    b.addEventListener("click", () => {
      localStorage.setItem("burnmeter_trend_tf", b.dataset.tf);
      document.querySelectorAll("#trend-picker button").forEach(x => x.classList.toggle("active", x.dataset.tf === b.dataset.tf));
      if (window.__lastReport) renderTrend(window.__lastReport, window.__source === "codex");
    });
  });
  // active-model window picker (15dk/1sa/5sa)
  let aw0 = localStorage.getItem("burnmeter_active_window3") || "live"; if (!["live", "1", "5", "15"].includes(aw0)) aw0 = "live";
  document.querySelectorAll("#active-window-picker button").forEach(b => {
    b.classList.toggle("active", b.dataset.w === aw0);
    b.addEventListener("click", () => {
      localStorage.setItem("burnmeter_active_window3", b.dataset.w);
      document.querySelectorAll("#active-window-picker button").forEach(x => x.classList.toggle("active", x.dataset.w === b.dataset.w));
      if (window.__lastReport) renderActiveModel(window.__lastReport);
    });
  });
  // trend MA toggle buttons (7/25/50/100, multi-select)
  let maSel = JSON.parse(localStorage.getItem("burnmeter_trend_ma") || "[7]");
  document.querySelectorAll(".indicator-btn").forEach(b => {
    const n = parseInt(b.dataset.ma, 10);
    b.classList.toggle("active", maSel.includes(n));
    b.addEventListener("click", () => {
      maSel = JSON.parse(localStorage.getItem("burnmeter_trend_ma") || "[7]");
      if (maSel.includes(n)) maSel = maSel.filter(x => x !== n); else maSel.push(n);
      localStorage.setItem("burnmeter_trend_ma", JSON.stringify(maSel));
      b.classList.toggle("active", maSel.includes(n));
      if (window.__lastReport) renderTrend(window.__lastReport, window.__source === "codex");
    });
  });
  initModularGrid();
  const rl = $("reset-layout");
  if (rl) rl.addEventListener("click", () => { localStorage.removeItem("burnmeter_layout_v4"); location.reload(); });
  // First paint is cold until the local logs are parsed — on large histories that
  // first read can take a few seconds. Say so instead of showing a blank "…" gauge.
  $("last-updated").textContent = "Reading your Burnmeter logs — the first run can take a minute or two on large histories…";
  refresh(false);
  setInterval(() => refresh(false), 10000);
  setInterval(updateLiveStamp, 1000);
  setInterval(tickLive, 1000);   // canlı aktif model: saniyelik nabız/şimdi güncellemesi
  setInterval(liveGaugeTick, 5000);   // burn-rate gauge decays live (→ $0 when idle)
  // Re-check for a new version every 3h so a long-open dashboard surfaces the
  // update pill on its own (not only on first load / the tray's 6h check).
  setInterval(() => {
    window.__updateChecked = false;
    const v = window.__lastReport?._meta?.version
          || window.__lastReport?.claude?._meta?.version
          || window.__lastReport?.codex?._meta?.version;
    if (v) checkForUpdate(v);
  }, 3 * 60 * 60 * 1000);
}
document.addEventListener("DOMContentLoaded", start);
