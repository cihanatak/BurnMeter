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

// ---------- view zoom (high-DPI friendliness; works in browser + native window) ----------
// On a 150%-scaled monitor the dashboard renders large; this lets the user scale
// it down (Ctrl +/-/0 or the header − %  + control) and remembers the choice.
// First run with no preference picks a smaller default on high-DPI screens.
(function () {
  const KEY = "burnmeter_zoom";
  const MIN = 0.5, MAX = 1.5, STEP = 0.1;
  const defaultZoom = () => {
    const dpr = window.devicePixelRatio || 1;
    if (dpr >= 1.5) return 0.8;
    if (dpr >= 1.25) return 0.9;
    return 1.0;
  };
  const clamp = (z) => Math.min(MAX, Math.max(MIN, Math.round(z * 100) / 100));
  const current = () => {
    const z = parseFloat(localStorage.getItem(KEY));
    return clamp(!z || isNaN(z) ? defaultZoom() : z);
  };
  const apply = (z) => {
    document.documentElement.style.zoom = z;          // Chromium/WebView2 CSS zoom
    const pct = document.getElementById("zoom-pct");
    if (pct) pct.textContent = Math.round(z * 100) + "%";
  };
  const setZoom = (z) => { z = clamp(z); localStorage.setItem(KEY, String(z)); apply(z); };
  window.bmZoomIn = () => setZoom(current() + STEP);
  window.bmZoomOut = () => setZoom(current() - STEP);
  window.bmZoomReset = () => setZoom(1.0);
  apply(current());                                    // apply ASAP (html exists)
  document.addEventListener("DOMContentLoaded", () => apply(current()));
  window.addEventListener("keydown", (e) => {
    if (!(e.ctrlKey || e.metaKey)) return;
    if (e.key === "+" || e.key === "=") { e.preventDefault(); window.bmZoomIn(); }
    else if (e.key === "-" || e.key === "_") { e.preventDefault(); window.bmZoomOut(); }
    else if (e.key === "0") { e.preventDefault(); window.bmZoomReset(); }
  });
})();

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
      else { paintCombinedActive("claude", bmScopedHalf(cl, "claude")); paintCombinedActive("codex", bmScopedHalf(cx, "codex")); }  // scoped → shows remote devices' live model
      window.__lastRefreshMs = Date.now();
      return;
    }
    const rep = await loadReport(force);
    if ((rep._meta?.source || "claude") !== window.__source) return; // stale
    window.__reportCache[rep._meta?.source || "claude"] = rep;
    window.__lastReport = rep;
    const sig = repSig(rep);
    if (sig !== window.__renderSig) { render(rep); window.__renderSig = sig; }
    else { renderActiveModel(bmScopedRep(rep) || rep); }   // scoped → keeps cross-device live model (no merged→local flicker)
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
// Split a project dir into { name (basename, shown big), sub (compact parent path, shown
// small underneath), full (the whole path, for the hover tooltip) }. So the user sees WHICH
// repo, not just an ambiguous "repo". Handles both / and \ and trims a long parent to the
// last 2 segments with a leading "…".
function bmPathParts(dir, label) {
  const raw = String(dir || "").trim();
  let name = label || "";
  if (!name && raw) name = raw.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || "";
  name = name || "unknown";
  if (!raw) return { name, sub: "", full: "" };
  const segs = raw.replace(/\\/g, "/").replace(/\/+$/, "").split("/").filter(Boolean);
  if (segs.length && segs[segs.length - 1] === name) segs.pop();   // drop basename → parent
  let sub = segs.slice(-2).join("/");
  if (segs.length > 2) sub = "…/" + sub;
  return { name, sub, full: raw };
}
// Project cell. With a CHAT TITLE (sohbet adı — what the user sees in the desktop sidebar):
// chat big, "folder · path" small below — one workspace hosts many chats, so the folder
// alone is ambiguous. Without a title (CLI-only session / Codex): folder big, path below.
function bmProjCell(dir, label, device, chat) {
  const pp = bmPathParts(dir, label);
  const badge = device ? " " + deviceBadge(device) : "";
  const name = chat || pp.name;
  const sub = chat ? [pp.name, pp.sub].filter(Boolean).join(" · ") : pp.sub;
  return `<div class="bm-proj" title="${esc(pp.full || pp.name)}">`
    + `<span class="bm-proj-name">${esc(name)}</span>${badge}`
    + (sub ? `<span class="bm-proj-sub">${esc(sub)}</span>` : "")
    + `</div>`;
}
function liveWhere(m) {
  const dev = deviceName(m.device);
  const pp = bmPathParts(m.project_dir, m.project);
  const name = m.chat || pp.name;
  const sub = m.chat ? [pp.name, pp.sub].filter(Boolean).join(" · ") : pp.sub;
  return `<span class="am-where" title="${esc(pp.full || pp.name)}">`
    + `<span class="am-proj"><span class="am-proj-icon">${m.chat ? "💬" : "📂"}</span><span class="am-projname">${esc(name)}</span>`
    + `${dev ? `<span class="am-dev">${esc(dev)}</span>` : ""}</span>`
    + (sub ? `<span class="am-projpath">${esc(sub)}</span>` : "")
    + `</span>`;
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

// After a one-click update an external updater stops the server, reinstalls, and
// relaunches on the same port (~30-90s incl. pip). Wait for it to go down and come
// back, then reload THIS tab onto the new version.
async function waitForRestartAndReload(latest) {
  let downSeen = false;
  for (let i = 0; i < 150; i++) {          // ~150s — pip clone+build can be slow
    await new Promise(r => setTimeout(r, 1000));
    try {
      const r = await fetch("/api/health", { cache: "no-store" });
      if (r.ok) {
        const d = await r.json().catch(() => ({}));
        if (downSeen || d.version === latest) { location.reload(); return; }
      }
    } catch (e) { downSeen = true; }   // server is down → mid-update
  }
  const el = $("update-link"); if (el) el.textContent = `✓ Updated to v${latest} — reload`;
}

// ===== multi-device scope (Pro) — the hero "fuel panel" reflects the whole fleet =====
// The source toggle answers "which tool?"; the scope pill answers "which machine?".
// Default = all devices combined (so adding a PC visibly changes the headline).
window.__scope = localStorage.getItem("burnmeter_scope") || "all";
window.__devicesCache = null;   // last /api/sync/devices payload (set by renderSyncDevices)

function bmActiveSources() {
  return window.__source === "both" ? ["claude", "codex"] : [window.__source];
}
function bmDeviceAgg(dev, sources) {
  const o = { burn: 0, today: 0, month: 0, lifetime: 0, tokens: 0, records: 0, chSum: 0, chW: 0 };
  (sources || []).forEach((s) => {
    const v = (dev.sources || {})[s]; if (!v) return;
    // Burn at the picker's window so a remote device matches what its own gauge shows
    // (snapshot carries burn_rates_by_hours; older snapshots only have burn_rate_per_hour).
    const hrs = localStorage.getItem("burnmeter_burn_hours_" + s) || "2";
    const wb = (v.burn_rates_by_hours && v.burn_rates_by_hours[hrs] != null) ? v.burn_rates_by_hours[hrs] : (v.burn_rate_per_hour || 0);
    o.burn += wb || 0; o.today += v.today_cost || 0; o.month += v.month_so_far || 0;
    o.lifetime += v.lifetime_cost || 0; o.tokens += v.lifetime_tokens || 0; o.records += v.record_count || 0;
    o.chSum += (v.cache_hit_rate || 0) * (v.record_count || 0); o.chW += v.record_count || 0;
  });
  o.cache = o.chW ? o.chSum / o.chW : 0;
  return o;
}
// Per-device aggregate, but the LOCAL device uses the LIVE report (its snapshot lags
// up to ~5 min, so "all devices" must not show a stale burn the per-device view shows
// as live). Remote devices use their snapshot. Burn is window-aware for the local
// device (matches the burn-window picker), so the All gauge agrees with the This gauge.
function bmDeviceAggFresh(dev, sources) {
  const payload = window.__devicesCache, lr = window.__lastReport;
  if (payload && dev.device_id === payload.this_device && lr && !lr._combined) {
    const fc = lr.forecast || {}, tot = lr.totals || {};
    const hrs = localStorage.getItem("burnmeter_burn_hours_" + (window.__source || "claude")) || "2";
    const burn = (fc.burn_rates_by_hours && fc.burn_rates_by_hours[hrs] != null)
      ? fc.burn_rates_by_hours[hrs] : (fc.burn_rate_per_hour_recent || 0);
    return { burn: burn || 0, today: (fc.today && fc.today.so_far) || 0, month: (fc.month && fc.month.so_far) || 0,
             lifetime: tot.cost_usd || 0, tokens: tot.total_tokens || 0, records: lr.record_count || 0,
             cache: tot.cache_hit_rate || 0 };
  }
  return bmDeviceAgg(dev, sources);
}
function bmMergedRecent(devs, sources, n) {
  const out = [];
  const payload = window.__devicesCache, lr = window.__lastReport;
  devs.forEach((d) => {
    // Local device → live recent_turns (full fields incl. cost/cache, always fresh).
    if (payload && d.device_id === payload.this_device && lr && !lr._combined && Array.isArray(lr.recent_turns)) {
      lr.recent_turns.forEach((t) => out.push({ ...t, device: d.label }));
      return;
    }
    // Remote device → snapshot recent. Map the compact fields to what renderRecent reads
    // (effective_tokens / cost_usd) — the old code only set total_tokens → showed 0 / $0.00.
    sources.forEach((s) => {
      const v = (d.sources || {})[s];
      if (v && v.recent) v.recent.forEach((t) => out.push({
        timestamp: t.ts, project_label: t.project, project_dir: t.project_dir, chat: t.chat, model: t.model,
        effective_tokens: t.tokens, total_tokens: t.tokens, cost_usd: t.cost,
        cache_hit_rate: null, device: d.label,
      }));
    });
  });
  out.sort((a, b) => new Date(b.timestamp || 0) - new Date(a.timestamp || 0));
  return out.slice(0, n || 12);
}
// Fleet cache-savings: SUM usd_saved across the picked devices (local from the live report,
// remote from each snapshot's cache_efficiency) so the KPI is the FLEET total, not local-only.
function bmMergeCache(pick, sources, thisId, localRep) {
  let usd_saved = 0, usd_on_cache = 0, full_equiv_usd = 0;
  (pick || []).forEach((d) => {
    if (d.device_id === thisId && localRep && localRep.cache_efficiency) {
      const ce = localRep.cache_efficiency;
      usd_saved += ce.usd_saved || 0; usd_on_cache += ce.usd_on_cache || 0; full_equiv_usd += ce.full_equiv_usd || 0;
    } else {
      sources.forEach((s) => {
        const ce = ((d.sources || {})[s] || {}).cache_efficiency;
        if (ce) { usd_saved += ce.usd_saved || 0; usd_on_cache += ce.usd_on_cache || 0; full_equiv_usd += ce.full_equiv_usd || 0; }
      });
    }
  });
  return { usd_saved, usd_on_cache, full_equiv_usd, discount_pct: full_equiv_usd ? usd_saved / full_equiv_usd : 0 };
}
// Fleet daily cost: sum per-day cost across devices (local full daily[], remote snapshot daily
// [{d,c}]) so the KPI sparklines show the FLEET trend instead of going blank in fleet scope.
function bmMergedDaily(pick, sources, n, thisId, localRep) {
  const byDate = new Map();
  const dk = (x) => String(x || "").slice(0, 10);   // normalize to YYYY-MM-DD so local/remote keys align
  (pick || []).forEach((d) => {
    if (d.device_id === thisId && localRep && Array.isArray(localRep.daily)) {
      localRep.daily.forEach((dd) => { const k = dk(dd.date); if (k) byDate.set(k, (byDate.get(k) || 0) + (dd.cost_usd || 0)); });
    } else {
      sources.forEach((s) => {
        const dl = ((d.sources || {})[s] || {}).daily;
        if (Array.isArray(dl)) dl.forEach((dd) => { const k = dk(dd.d); if (k) byDate.set(k, (byDate.get(k) || 0) + (dd.c || 0)); });
      });
    }
  });
  return [...byDate.entries()].filter(([k]) => k)
    .sort((a, b) => String(a[0]).localeCompare(String(b[0]))).slice(-(n || 30))
    .map(([date, cost_usd]) => ({ date, cost_usd }));
}
// Build a hero-shaped view for scope = "all" or a remote device_id; null → use local.
function bmScopedRep(localRep) {
  const scope = window.__scope || "all";
  const payload = window.__devicesCache;
  const devices = (payload && payload.devices ? payload.devices.filter((d) => !d._undecryptable) : []);
  if (scope === "this" || !devices.length) { window.__scopedAsOf = null; return null; }
  const sources = bmActiveSources();
  let agg, label, k = 1;
  if (scope === "all") {
    agg = { burn: 0, today: 0, month: 0, lifetime: 0, tokens: 0, records: 0, chSum: 0, chW: 0 };
    let active = 0;
    devices.forEach((d) => {
      const a = bmDeviceAggFresh(d, sources);
      agg.burn += a.burn; agg.today += a.today; agg.month += a.month; agg.lifetime += a.lifetime;
      agg.tokens += a.tokens; agg.records += a.records; agg.chSum += a.cache * a.records; agg.chW += a.records;
      if (a.burn > 0.01) active++;
    });
    agg.cache = agg.chW ? agg.chSum / agg.chW : 0;
    k = Math.max(1, active); label = "All devices";
  } else {
    const d = devices.find((x) => x.device_id === scope);
    if (!d) return null;
    agg = bmDeviceAgg(d, sources); label = d.label || "device";
  }
  const z0 = localRep.burn_rate_zones || { typical: 4, busy: 12, heavy: 25, max: 50 };
  const zones = {
    typical: z0.typical * k, busy: z0.busy * k, heavy: z0.heavy * k,
    max: (z0.max || 50) * k, insufficient_history: z0.insufficient_history,
  };
  const pick = scope === "all" ? devices : devices.filter((d) => d.device_id === scope);
  const thisId = payload && payload.this_device;
  // Staleness: oldest REMOTE snapshot in scope (local device is live). updateLiveStamp shows
  // "synced Xm ago" so a remote/fleet view doesn't masquerade as live "now".
  // Staleness label ONLY for a specific REMOTE device (all its data is as-of-last-sync). For
  // "all" the local half is LIVE, so labeling the whole view "synced Xm ago" reads as a false
  // delay — keep the live local stamp there (each device card still shows its own "ago").
  let asOf = null;
  if (scope !== "all") pick.forEach((d) => { if (d.device_id !== thisId && d._updated_at && (!asOf || d._updated_at < asOf)) asOf = d._updated_at; });
  window.__scopedAsOf = asOf;
  return {
    __scopeLabel: label,
    _meta: localRep._meta,
    burn_rate_zones: zones,
    forecast: { burn_rate_per_hour_recent: agg.burn, recent_costs: null,
                today: { so_far: agg.today }, month: { so_far: agg.month } },
    totals: { cost_usd: agg.lifetime, total_tokens: agg.tokens, cache_hit_rate: agg.cache },
    record_count: agg.records,
    cache_efficiency: Object.assign(bmMergeCache(pick, sources, thisId, localRep), { hit_rate: agg.cache }),  // FLEET cache savings (sum) + fleet weighted hit-rate
    daily: bmMergedDaily(pick, sources, 30, thisId, localRep),         // FLEET daily trend (sparkline)
    recent_turns: bmMergedRecent(pick, sources, 12),
    live_active_models_by_window: bmMergeLive(localRep.live_active_models_by_window, pick, sources[0], thisId),
  };
}
function bmRefreshHero() {
  const rep = window.__lastReport; if (!rep || rep._combined) return;  // scope = single-source hero
  const isCodex = (rep._meta?.source || "claude") === "codex";
  const h = bmScopedRep(rep) || rep;
  renderSpeedometer(h, isCodex);
  renderKPIs(h, isCodex);
  renderCache(h);        // MUST re-render on scope change too — else the cache card freezes at
  renderDaily(h);        // the this-device value while the KPI shows fleet (a visible mismatch).
  renderRecent(h);
  renderDeviceBreakdown();
  renderOverviewFleet();
  renderProjects(rep);   // scope-aware: fleet rollup vs this-device
}

// Overview device selector — "All devices" + one chip per machine (name + this-month $).
// Shows the fleet right on Overview and IS the scope picker; click → scope the panel.
function renderOverviewFleet() {
  const el = $("overview-fleet"); if (!el) return;
  const payload = window.__devicesCache;
  const devs = (payload && payload.devices ? payload.devices.filter((d) => !d._undecryptable) : []);
  if (devs.length < 2 || (window.__lastReport && window.__lastReport._combined)) { el.style.display = "none"; return; }
  const sources = bmActiveSources();
  const scope = window.__scope || "all";
  const total = devs.reduce((s, d) => s + bmDeviceAggFresh(d, sources).month, 0);
  let html = `<button class="ovf-chip${scope === 'all' ? ' on' : ''}" onclick="bmSetScope('all')">` +
    `<span class="ovf-ic" aria-hidden="true">🖥</span><span class="ovf-nm">All devices</span><span class="ovf-v">${fmtMoney0(total)}</span></button>`;
  devs.forEach((d) => {
    const isThis = d.device_id === payload.this_device;
    const id = bmSid(isThis ? "this" : d.device_id);
    const ic = BM_OSICON[d.os] || "🖥";
    html += `<button class="ovf-chip${scope === id ? ' on' : ''}" data-scope="${esc(id)}" onclick="bmToggleScope('${id}')">` +
      `<span class="ovf-ic" aria-hidden="true">${ic}</span><span class="ovf-nm">${esc(d.label || 'device')}${isThis ? ' ·this' : ''}</span>` +
      `<span class="ovf-v">${fmtMoney0(bmDeviceAggFresh(d, sources).month)}</span></button>`;
  });
  el.innerHTML = html;
  el.style.display = "";
}

// Scope ids are interpolated into inline onclick/data-scope. A synced device_id
// comes from another machine's snapshot (attacker-controlled if a co-account/Firebase
// is compromised), so whitelist it to a safe charset — esc() alone is NOT enough in a
// JS-handler context (the browser entity-decodes before running the handler). Legit ids
// are uuid hex and "all"/"this", so this is a no-op for honest data.
const bmSid = (x) => String(x == null ? "" : x).replace(/[^A-Za-z0-9_-]/g, "");

// Per-device share of the headline (this month $) — a stacked bar under the hero.
// The breakdown IS the picker: click a segment/legend to scope the hero to it.
const BM_DEVCOLORS = ["#5E6AD2", "#10A37F", "#D97757", "#F5A623", "#2BD96B", "#9b6dff", "#e0567f", "#4cb3ff"];
function renderDeviceBreakdown() {
  const el = $("device-breakdown"); if (!el) return;
  const payload = window.__devicesCache;
  const devs = (payload && payload.devices ? payload.devices.filter((d) => !d._undecryptable) : []);
  if (devs.length < 2 || (window.__lastReport && window.__lastReport._combined)) { el.style.display = "none"; return; }
  const sources = bmActiveSources();
  const parts = devs.map((d, i) => {
    const isThis = d.device_id === payload.this_device;
    return { id: bmSid(isThis ? "this" : d.device_id), label: d.label || "device",
             val: bmDeviceAggFresh(d, sources).month, color: BM_DEVCOLORS[i % BM_DEVCOLORS.length], me: isThis };
  }).sort((a, b) => b.val - a.val);
  const total = parts.reduce((s, p) => s + p.val, 0) || 1;
  const active = window.__scope;
  el.style.display = "";
  el.innerHTML =
    `<div class="dbk-head"><span class="dbk-t">This month by device</span>` +
    `<span class="dbk-sum">${fmtMoney0(total)} · ${devs.length} devices</span></div>` +
    `<div class="dbk-bar">` + parts.map((p) =>
      `<span class="dbk-seg${active === p.id ? ' on' : ''}" style="width:${Math.max(2, p.val / total * 100).toFixed(1)}%;background:${p.color}" title="${esc(p.label)} · ${fmtMoney(p.val)}" onclick="bmToggleScope('${p.id}')"></span>`).join("") +
    `</div><div class="dbk-legend">` +
    `<span class="dbk-li${active === 'all' ? ' on' : ''}" onclick="bmSetScope('all')"><span class="dbk-dot" style="background:linear-gradient(90deg,#5E6AD2,#10A37F)"></span>All devices</span>` +
    parts.map((p) =>
      `<span class="dbk-li${active === p.id ? ' on' : ''}" onclick="bmToggleScope('${p.id}')"><span class="dbk-dot" style="background:${p.color}"></span>${esc(p.label)}${p.me ? ' ·this' : ''} <b>${fmtMoney0(p.val)}</b></span>`).join("") +
    `</div>`;
}
function populateScopePill(payload) {
  const sel = $("scope-select"), wrap = $("scope-wrap");
  if (!sel || !wrap) return;
  const devs = (payload && payload.devices ? payload.devices.filter((d) => !d._undecryptable) : []);
  const badge = $("nav-dev-count");   // device count on the sidebar "Devices" item
  if (badge) { if (devs.length) { badge.textContent = String(devs.length); badge.style.display = ""; } else { badge.style.display = "none"; } }
  if (devs.length <= 1) { wrap.style.display = "none"; return; }   // only meaningful with 2+
  wrap.style.display = "";
  const opts = ['<option value="all">All devices</option>'];
  devs.forEach((d) => {
    const isThis = d.device_id === payload.this_device;
    const val = bmSid(isThis ? "this" : d.device_id);
    opts.push(`<option value="${esc(val)}">${esc(d.label || "device")}${isThis ? " (this)" : ""}</option>`);
  });
  sel.innerHTML = opts.join("");
  const valid = ["all", "this", ...devs.map((d) => bmSid(d.device_id))];
  if (!valid.includes(window.__scope)) window.__scope = "all";
  sel.value = window.__scope;
}
window.bmSetScope = function (v) {
  window.__scope = bmSid(v) || "all";
  localStorage.setItem("burnmeter_scope", window.__scope);
  const sel = $("scope-select"); if (sel && sel.value !== window.__scope) sel.value = window.__scope;
  const lr = window.__lastReport;
  if (lr && lr._combined) renderCombined(lr.claude, lr.codex);   // "both" view re-scopes
  else bmRefreshHero();
  bmHighlightScope();
};
// Click a device card / breakdown segment again to go back to the whole fleet.
window.bmToggleScope = function (id) { bmSetScope(window.__scope === id ? "all" : id); };
function bmHighlightScope() {
  document.querySelectorAll("[data-scope]").forEach((el) =>
    el.classList.toggle("scoped", el.getAttribute("data-scope") === window.__scope));
}

function render(rep) {
  { const cv = $("combined-view"); if (cv) cv.style.display = "none";
    const mn = document.querySelector("main"); if (mn) mn.style.display = ""; }
  const heroRep = bmScopedRep(rep) || rep;
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
  renderSpeedometer(heroRep, isCodex);   // hero gauge = scoped (all devices / one device)
  renderHeroAside(rep, isCodex);         // "Local usage" stays this-device
  renderKPIs(heroRep, isCodex);          // KPI strip = scoped fleet totals
  renderEfficiency(rep, isCodex);        // per-device deep-dive (fuel_efficiency not in snapshot)
  renderCache(heroRep);                  // cache card = scoped → matches the fleet KPI $
  renderBudget(rep, isCodex);
  renderSyncDevices();
  renderTrend(rep, isCodex);             // burn HISTORY (timeseries not in snapshot) → this-device
  renderModels(rep);
  renderDaily(heroRep);                  // daily bars = scoped → matches the fleet KPI sparkline
  renderProjects(rep);
  renderHeatmap(rep);
  renderRecent(heroRep);                 // recent reflects the scope
  renderModelTable(rep);
  renderBehavior(rep);
  renderTools(rep);
  renderActiveModel(heroRep);            // live model = scoped (shows other devices' running model)
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
      // Repaint the SAME device-scoped halves renderCombined uses — NOT the raw local
      // report halves. Using rep.claude/rep.codex here clobbered the cross-device
      // aggregate every 5s with the LOCAL-only value (combined Codex→$0 on a PC not
      // running Codex; Claude→$0 on the Mac) → the "correct then $0" flicker.
      [["claude", bmScopedHalf(rep.claude, "claude"), false], ["codex", bmScopedHalf(rep.codex, "codex"), true]].forEach(([side, r, isCodex]) => {
        const root = document.getElementById("cv-" + side); if (!root || !r) return;
        paintBurnGauge({
          svg: root.querySelector(".cv-speedo"),
          zone: root.querySelector(".cv-zone2"),
          breakdown: root.querySelector(".cv-breakdown"),
          title: root.querySelector(".cvb-trow .cvb-t"),
        }, r, isCodex, localStorage.getItem("burnmeter_burn_hours_" + side) || "2");
      });
    } else {
      renderSpeedometer(bmScopedRep(rep) || rep, window.__source === "codex");
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
// A per-source report scoped to the device selection, for the combined ("both") view.
// scope "this"/no-devices → the live local report. Else merge that source across the
// picked devices: local device = LIVE (from the combined report's half), remote = its
// snapshot. Mirrors bmScopedRep but for one named source.
function bmScopedHalf(localRep, source) {
  const scope = window.__scope || "all";
  const payload = window.__devicesCache;
  const devices = (payload && payload.devices ? payload.devices.filter((d) => !d._undecryptable) : []);
  if (scope === "this" || !devices.length) return localRep;
  const pick = scope === "all" ? devices : devices.filter((d) => d.device_id === scope);
  if (!pick.length) return localRep;
  // Aggregate burn at EVERY window (not one) so the gauge matches each device's own gauge
  // at any picker position AND sums across devices. CRITICAL: we null recent_costs below
  // so liveBurnRate() reads this aggregate instead of recomputing from local turns only
  // (that bug made "both"-tab Codex show $0 on the PC / undercount on the Mac).
  const WINS = ["0.0833", "0.25", "1", "2", "4", "6"];
  const hrs = localStorage.getItem("burnmeter_burn_hours_" + source) || "2";
  const burnByH = {}; WINS.forEach((w) => (burnByH[w] = 0));
  let today = 0, month = 0, lifetime = 0, tokens = 0, records = 0, chSum = 0, chW = 0, k = 0;
  const recent = [];
  pick.forEach((d) => {
    const isThis = payload && d.device_id === payload.this_device;
    if (isThis && localRep && !localRep._combined) {
      const fc = localRep.forecast || {}, tot = localRep.totals || {};
      const bbh = fc.burn_rates_by_hours || {};
      WINS.forEach((w) => { if (bbh[w] != null) burnByH[w] += bbh[w] || 0; });
      today += (fc.today && fc.today.so_far) || 0; month += (fc.month && fc.month.so_far) || 0;
      lifetime += tot.cost_usd || 0; tokens += tot.total_tokens || 0; records += localRep.record_count || 0;
      chSum += (tot.cache_hit_rate || 0) * (localRep.record_count || 0); chW += localRep.record_count || 0;
      if ((bbh[hrs] || 0) > 0.01) k++;
      (localRep.recent_turns || []).forEach((t) => recent.push({ ...t, device: d.label }));
    } else {
      const v = (d.sources || {})[source]; if (!v) return;
      const bbh = v.burn_rates_by_hours || {};
      if (Object.keys(bbh).length) WINS.forEach((w) => { if (bbh[w] != null) burnByH[w] += bbh[w] || 0; });
      else if (v.burn_rate_per_hour) burnByH[hrs] += v.burn_rate_per_hour || 0;  // pre-0.1.63 snapshot
      today += v.today_cost || 0; month += v.month_so_far || 0;
      lifetime += v.lifetime_cost || 0; tokens += v.lifetime_tokens || 0; records += v.record_count || 0;
      chSum += (v.cache_hit_rate || 0) * (v.record_count || 0); chW += v.record_count || 0;
      if ((bbh[hrs] != null ? bbh[hrs] : (v.burn_rate_per_hour || 0)) > 0.01) k++;
      (v.recent || []).forEach((t) => recent.push({
        timestamp: t.ts, project_label: t.project, model: t.model,
        effective_tokens: t.tokens, total_tokens: t.tokens, cost_usd: t.cost, cache_hit_rate: null, device: d.label,
      }));
    }
  });
  recent.sort((a, b) => new Date(b.timestamp || 0) - new Date(a.timestamp || 0));
  const kk = Math.max(1, scope === "all" ? k : 1);
  const z0 = localRep.burn_rate_zones || { typical: 4, busy: 12, heavy: 25, max: 50 };
  return {
    ...localRep,
    __scopeLabel: scope === "all" ? "All devices" : (pick[0].label || "device"),
    // recent_costs:null → liveBurnRate falls through to burn_rates_by_hours (the cross-device
    // aggregate). by_model breakdown nulled — it'd otherwise show only the local device's models.
    forecast: { ...(localRep.forecast || {}), recent_costs: null,
                burn_rates_by_hours: burnByH, burn_rates_by_hours_by_model: {},
                burn_rate_per_hour_recent: burnByH[hrs] || 0,
                today: { so_far: today }, month: { so_far: month } },
    totals: { ...(localRep.totals || {}), cost_usd: lifetime, total_tokens: tokens, cache_hit_rate: chW ? chSum / chW : 0 },
    record_count: records,
    burn_rate_zones: { typical: z0.typical * kk, busy: z0.busy * kk, heavy: z0.heavy * kk,
                       max: (z0.max || 50) * kk, insufficient_history: z0.insufficient_history },
    recent_turns: recent.slice(0, 12),
    // Live "what's running now" merged across devices (local windows + remote snapshots).
    live_active_models_by_window: bmMergeLive(localRep.live_active_models_by_window, pick, source, payload && payload.this_device),
  };
}

// Merge each picked device's currently-active models into a live_active_models_by_window
// shape so the "Live active model" panel shows REMOTE devices' running models too. Local
// device keeps its own live windows; a remote device contributes its snapshot `live` list
// (seconds_since_last recomputed from last_seen against THIS viewer's clock, filtered per
// window — so a model the Mac ran 3m ago shows in 5m/15m but not the 1m view).
function bmMergeLive(localLAM, pick, source, thisId) {
  const WSEC = { "1": 60, "5": 300, "15": 900, "60": 3600, "300": 18000, "1440": 86400 };
  const now = Date.now();
  const out = {};
  Object.keys(WSEC).forEach((w) => {
    const lim = WSEC[w];
    const models = ((((localLAM || {})[w]) || {}).models || []).slice();
    (pick || []).forEach((d) => {
      if (!d || d.device_id === thisId) return;   // local device already in localLAM
      const v = (d.sources || {})[source];
      const live = v && Array.isArray(v.live) ? v.live : [];
      live.forEach((m) => {
        const ls = m.last_seen ? new Date(m.last_seen).getTime() : NaN;
        const ssl = isFinite(ls) ? Math.max(0, (now - ls) / 1000) : 9e9;
        if (ssl > lim) return;
        models.push({
          model_id: m.model_id, device: d.label, project: m.project, project_dir: m.project_dir, chat: m.chat,
          tokens_per_min: m.tokens_per_min || 0, messages: m.messages || 0,
          cost_per_hour: m.cost_per_hour || 0, last_seen: m.last_seen,
          seconds_since_last: Math.round(ssl),
        });
      });
    });
    models.sort((a, b) => (a.seconds_since_last ?? 9e9) - (b.seconds_since_last ?? 9e9));
    out[w] = { models };
  });
  return out;
}

// Device selector (scroll) for the combined view: All devices + per device (combined
// Claude+Codex $). Click → scope; the combined halves re-render scoped.
function renderCombinedFleet() {
  const el = $("cv-fleet"); if (!el) return;
  const payload = window.__devicesCache;
  const devs = (payload && payload.devices ? payload.devices.filter((d) => !d._undecryptable) : []);
  if (devs.length < 2) { el.style.display = "none"; return; }
  const lr = window.__lastReport, scope = window.__scope || "all";
  const devMonth = (d) => {
    if (payload && d.device_id === payload.this_device && lr && lr._combined) {
      return ((lr.claude && lr.claude.forecast && lr.claude.forecast.month && lr.claude.forecast.month.so_far) || 0)
           + ((lr.codex && lr.codex.forecast && lr.codex.forecast.month && lr.codex.forecast.month.so_far) || 0);
    }
    return ((d.sources && d.sources.claude && d.sources.claude.month_so_far) || 0)
         + ((d.sources && d.sources.codex && d.sources.codex.month_so_far) || 0);
  };
  const total = devs.reduce((s, d) => s + devMonth(d), 0);
  let html = `<button class="ovf-chip${scope === 'all' ? ' on' : ''}" onclick="bmSetScope('all')">` +
    `<span class="ovf-ic" aria-hidden="true">🖥</span><span class="ovf-nm">All devices</span><span class="ovf-v">${fmtMoney0(total)}</span></button>`;
  devs.forEach((d) => {
    const isThis = payload && d.device_id === payload.this_device;
    const id = bmSid(isThis ? "this" : d.device_id);
    const ic = BM_OSICON[d.os] || "🖥";
    html += `<button class="ovf-chip${scope === id ? ' on' : ''}" onclick="bmToggleScope('${id}')">` +
      `<span class="ovf-ic" aria-hidden="true">${ic}</span><span class="ovf-nm">${esc(d.label || 'device')}${isThis ? ' ·this' : ''}</span>` +
      `<span class="ovf-v">${fmtMoney0(devMonth(d))}</span></button>`;
  });
  el.innerHTML = html;
  el.style.display = "";
}

function renderCombined(cl, cx) {
  const cv = $("combined-view"), mn = document.querySelector("main");
  if (mn) mn.style.display = "none";
  if (cv) cv.style.display = "flex";
  // combined view replaces the sections → no section is "current"; don't lie in the nav
  document.querySelectorAll(".nav-item[data-section]").forEach((b) => b.classList.remove("active"));
  document.documentElement.style.setProperty("--brand", "var(--brand-claude)");
  $("app-name").textContent = "Burnmeter"; $("brand-logo").textContent = "◫";
  document.title = "Burnmeter — Claude + Codex";
  $("version").textContent = "v" + (cl._meta?.version || cx._meta?.version || "?");
  { const v = cl._meta?.version || cx._meta?.version; if (v) { _showCheckAffordance(v); checkForUpdate(v); } }
  window.__multiDevice = new Set([...Object.keys(cl.by_device || {}), ...Object.keys(cx.by_device || {})]).size > 1;
  $("data-source").textContent = `Claude ${fmtInt(cl.record_count || 0)} · Codex ${fmtInt(cx.record_count || 0)} records`;
  $("last-updated").textContent = "now";
  // Device-aware: each half reflects the device scope (default all devices combined).
  const scl = bmScopedHalf(cl, "claude"), scx = bmScopedHalf(cx, "codex");
  renderCombinedFleet();
  const a = $("cv-claude"); if (a) a.innerHTML = halfStructure(scl, false, "claude");
  const b = $("cv-codex");  if (b) b.innerHTML = halfStructure(scx, true, "codex");
  paintCombinedSide("claude", scl, false);
  paintCombinedSide("codex", scx, true);
  // "both" mode never runs render()/renderSyncDevices, so load the fleet once here so
  // the device selector + all-devices merge work (it re-renders this view when ready).
  if (!window.__devicesCache) renderSyncDevices();
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
  // Prefer the server's per-window value — it's the SAME source the model breakdown, the
  // all-devices aggregate, and each device's contribution use. Recomputing from recent_costs
  // here made the needle live-decay (browser clock) out of step with those frozen values
  // between 10s refreshes (gauge $13 vs breakdown/all-devices $16 on one screen).
  const byh = fc.burn_rates_by_hours;
  if (byh && byh[hoursStr] != null) return byh[hoursStr];
  const rc = fc.recent_costs, hours = parseFloat(hoursStr) || 2;
  if (Array.isArray(rc)) {
    const cut = Date.now() / 1000 - hours * 3600;
    let cost = 0;
    for (let i = 0; i < rc.length; i++) if (rc[i][0] >= cut) cost += rc[i][1];
    return cost / hours;
  }
  return fc.burn_rate_per_hour_recent ?? 0;
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
  const recRows = recent.length ? recent.map(t => `<div class="cvr-row"><span class="cvr-when dim">${ago(t.timestamp)}</span><span class="cvr-proj" title="${esc(t.project_dir || t.project_label || "")}"><span class="cvr-projname">${esc(t.chat || t.project_label || "?")}</span>${deviceBadge(t.device)}</span><span class="cvr-model ${modelToFamily(t.model)}">${esc(modelDisplay(t.model))}</span><span class="cvr-tok num">${fmtInt(t.total_tokens || 0)}</span></div>`).join("") : `<div class="dim" style="padding:6px 0">no activity</div>`;
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
  const est = isCodex ? " (est.)" : "";
  // Burn rate KPI = the SAME window the gauge shows (the picker), not a fixed 2h — else the
  // card and the gauge disagree on one screen. scope=all → bmScopedRep has no
  // burn_rates_by_hours but burn_rate_per_hour_recent is already the picker-window aggregate.
  const bhrs = localStorage.getItem("burnmeter_burn_hours_" + (rep._meta?.source || "claude")) || "2";
  const burn = (fc.burn_rates_by_hours && fc.burn_rates_by_hours[bhrs] != null)
    ? fc.burn_rates_by_hours[bhrs] : (fc.burn_rate_per_hour_recent ?? 0);
  const bwin = bhrs === "0.0833" ? "5m" : bhrs === "0.25" ? "15m" : bhrs + "h";
  const kpis = [
    { l: "Spent today", v: "~" + fmtMoney(fc.today?.so_far ?? 0), s: `${fmtInt(todayE.messages || 0)} prompts`, spark: daily.slice(-14).map(d => d.cost_usd || 0) },
    { l: "Burn rate", v: "~" + fmtMoney(burn), unit: "/hr", s: `avg over last ${bwin}` + est, spark: null },
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
         <div class="dim" style="font-size:12.5px;line-height:1.5;max-width:560px">
           ${budget > 0 ? "Update your monthly budget." : "Set a monthly $ budget to track spend vs your limit."}
           <span style="color:var(--text-4)">Saved in this browser · estimates.</span>
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

// ---------- Pro cross-device sync — Firebase account + E2E-encrypted device summaries ----
// Account = Firebase Auth (email + password, real email verification). Usage snapshots
// live in Firestore as CIPHERTEXT ONLY — the local server holds the E2E key and decrypts
// for this 127.0.0.1 UI. The password is POSTed only to localhost, never stored.
const _proHdr = { "Content-Type": "application/json", "X-Burnmeter-Pro": "1" };

async function renderSyncDevices() {
  const body = $("sync-devices-body");
  if (!body) return;
  // Don't blow away an in-progress rename on the 10s render tick.
  const ae = document.activeElement;
  if (ae && ae.classList && ae.classList.contains("sync-rename-in")) return;
  let st;
  try { st = await (await fetch("/api/pro/status")).json(); }
  catch (e) { return; }   // server may be old → skip silently
  if (!st.configured) { body.innerHTML = proAuthFormHtml(); window.__devicesCache = { devices: [] }; return; }
  if (!st.verified) { body.innerHTML = proVerifyHtml(st.email); window.__devicesCache = { devices: [] }; return; }
  // signed in + verified → show devices
  let data;
  try { data = await (await fetch("/api/sync/devices")).json(); }
  catch (e) { data = { error: "server" }; }
  body.innerHTML = proSignedInHeader(st) + proDevicesHtml(data);
  // feed the multi-device scope: cache devices, fill the scope pill, re-scope whatever
  // view is showing (the combined "both" view OR the single-source hero).
  if (data && !data.error) {
    window.__devicesCache = data;
    populateScopePill(data);
    if (window.__lastReport && window.__lastReport._combined) renderCombined(window.__lastReport.claude, window.__lastReport.codex);
    else bmRefreshHero();
  }
}

function proAuthFormHtml() {
  return `<div style="max-width:560px">
    <div style="font-size:14.5px;font-weight:600;color:var(--text-1);margin-bottom:4px">Connect Pro <span style="color:var(--brand)">✦</span></div>
    <div class="dim" style="font-size:12.5px;line-height:1.55;margin-bottom:12px">See all your machines in one view — end-to-end encrypted (only summaries sync; your raw logs never leave your machine). Create an account or sign in with your <b>email + password</b>.</div>
    <div class="pro-form">
      <input id="pro-email" type="email" placeholder="email" value="${esc(localStorage.getItem('bm_pro_email') || '')}">
      <input id="pro-pass" type="password" placeholder="password (min 10 chars)">
      <button id="pro-signup" class="get-pro-btn" onclick="proAuth('signup')">Create account</button>
      <button id="pro-login" class="ghost-btn" onclick="proAuth('login')">Sign in</button>
      <span id="pro-msg" class="dim" style="font-size:12px"></span>
    </div>
    <div class="dim" style="font-size:11.5px;margin-top:10px">New here? <b>Create account</b> → we email you a verification link. Forgot your password loses synced history (it's the encryption key). <a href="https://burnmeter.dev/#pricing" target="_blank" rel="noopener" style="color:var(--brand)">About Pro →</a></div>
  </div>`;
}

function proVerifyHtml(email) {
  return `<div style="max-width:560px">
    <div style="font-size:14.5px;font-weight:600;color:var(--text-1);margin-bottom:4px">Verify your email <span style="color:var(--brand)">✦</span></div>
    <div class="dim" style="font-size:12.5px;line-height:1.55;margin-bottom:12px">We sent a verification link to <b>${esc(email || '')}</b>. Click it, then come back and continue. (Pro sync starts working once your email is verified.)</div>
    <div class="pro-form">
      <button class="get-pro-btn" onclick="renderSyncDevices()">I've verified — continue</button>
      <button class="ghost-btn" onclick="proResend()">Resend email</button>
      <button class="ghost-btn" onclick="proSignout()">Sign out</button>
      <span id="pro-msg" class="dim" style="font-size:12px"></span>
    </div>
  </div>`;
}

function proSignedInHeader(st) {
  const isPro = st.plan && st.plan !== "free";
  const pill = isPro
    ? `<span class="pro-plan active">● ${esc(st.plan)} plan</span>`
    : `<span class="pro-plan none">no plan yet</span>`;
  return `<div class="pro-bar">
    <span class="pro-badge">✦ Pro</span>
    <span class="pro-who">${esc(st.email || '')}</span>
    ${pill}
    <span id="pro-msg" class="dim" style="font-size:11.5px"></span>
    <span style="margin-left:auto;display:flex;gap:8px">
      <button class="ghost-btn" onclick="proPush(this)">Push now</button>
      <button class="ghost-btn" onclick="proSignout()">Sign out</button>
    </span></div>`;
}

async function proPush(btn) {
  if (btn) { btn.disabled = true; btn.textContent = "Pushing…"; }
  try { await fetch("/api/pro/push", { method: "POST", headers: _proHdr }); } catch (e) {}
  renderSyncDevices();   // server busts the devices cache on push → fresh list
}
window.proPush = proPush;

const BM_OSICON = { Windows: "🪟", Darwin: "🍎", Linux: "🐧" };
function bmOnlineState(iso) {
  if (!iso) return { cls: "off", txt: "—" };
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 360) return { cls: "live", txt: "online" };       // pushed within ~6 min
  if (s < 86400) return { cls: "idle", txt: "idle" };
  return { cls: "off", txt: "offline" };
}
function proDevicesHtml(data) {
  if (data && data.error) return `<div class="dim" style="font-size:12px;color:var(--warn)">couldn't reach Firebase: ${esc(String(data.error))}</div>`;
  const devs = (data && data.devices) || [];
  if (!devs.length) return `<div class="dim" style="font-size:12.5px">no synced devices yet — click <b>Push now</b> above, or this device pushes automatically within ~5 min.</div>`;
  const ago = (iso) => { if (!iso) return ""; const s = (Date.now() - new Date(iso).getTime()) / 1000; return s < 60 ? "now" : s < 3600 ? Math.round(s / 60) + "m" : s < 86400 ? Math.round(s / 3600) + "h" : Math.round(s / 86400) + "d"; };
  return `<div class="sync-grid">` + devs.map(d => {
    if (d._undecryptable) {
      return `<div class="sync-dev"><div class="sync-dev-head"><span class="sync-dot bad"></span><b>${esc(d.device_id || "?")}</b></div>
        <div class="dim" style="font-size:11px">couldn't decrypt · different password?</div></div>`;
    }
    const me = d.device_id === data.this_device;
    const scopeId = bmSid(me ? "this" : d.device_id);   // safe for inline onclick (see bmSid)
    const on = window.__scope === scopeId;
    const st = bmOnlineState(d._updated_at);
    const osi = BM_OSICON[d.os] || "🖥";
    const srcs = d.sources || {};
    const rows = Object.keys(srcs).map(s => {
      const v = srcs[s] || {};
      return `<div class="sync-src"><span class="sync-srcname ${s === 'codex' ? 'fam-gpt' : 'fam-opus'}">${esc(s)}</span>
        <span class="sync-amt">~${fmtMoney(v.month_so_far || 0)}<small>this month</small></span>
        <span class="sync-amt">~${fmtMoney(v.burn_rate_per_hour || 0)}<small>/hr now</small></span>
        <span class="sync-cnt">${fmtInt(v.record_count || 0)}<small>records</small></span></div>`;
    }).join("") || `<div class="dim" style="font-size:11px">no data</div>`;
    const recent = Object.keys(srcs)
      .flatMap(s => (srcs[s].recent || []).map(t => ({ ...t, _src: s })))
      .sort((a, b) => new Date(b.ts || 0) - new Date(a.ts || 0)).slice(0, 6);
    const recentHtml = recent.length ? `<div class="sync-recent">` + recent.map(t =>
      `<div class="sync-rrow"><span class="sync-rago dim">${ago(t.ts)}</span>` +
      `<span class="sync-rproj">${esc(t.project || "?")}</span>` +
      `<span class="sync-rmodel ${t._src === 'codex' ? 'fam-gpt' : 'fam-opus'}">${esc(modelDisplay(t.model))}</span>` +
      `<span class="sync-rtok num">${fmtInt(t.tokens || 0)}</span></div>`
    ).join("") + `</div>` : "";
    const pencil = me ? `<button class="sync-rename" title="rename this device" onclick="event.stopPropagation();bmRenameDevice(this)">✎</button>` : "";
    return `<div class="sync-dev${me ? ' me' : ''}${on ? ' scoped' : ''}" data-scope="${esc(scopeId)}" title="show only this device in the fuel panel" onclick="bmToggleScope('${scopeId}')">
      <div class="sync-dev-head"><span class="sync-dot ${st.cls}"></span><span class="sync-os" title="${esc(d.os || '')}">${osi}</span><b>${esc(d.label || "?")}</b>${me ? '<span class="sync-badge">this device</span>' : ''}${pencil}<span class="sync-ago dim">${st.txt} · ${ago(d._updated_at)}</span></div>
      ${rows}${recentHtml}</div>`;
  }).join("") + `</div>`;
}

// Inline rename of THIS device (pencil on the this-device card). Avoids window.prompt
// (unreliable under pywebview) — swaps the name for an input, commits on Enter/blur.
window.bmRenameDevice = function (btn) {
  const head = btn.closest(".sync-dev-head"); if (!head) return;
  const nameEl = head.querySelector("b"); if (!nameEl || head.querySelector(".sync-rename-in")) return;
  const inp = document.createElement("input");
  inp.className = "sync-rename-in"; inp.value = nameEl.textContent; inp.maxLength = 40;
  inp.onclick = (e) => e.stopPropagation();
  // Escape cancels. Must blur FIRST: renderSyncDevices skips while a rename input is
  // focused (so the 10s tick can't clobber a mid-edit), and Escape/Enter don't blur
  // a text input on their own — without the blur the revert re-render would self-skip.
  inp.onkeydown = (e) => { e.stopPropagation(); if (e.key === "Enter") bmRenameCommit(inp); else if (e.key === "Escape") { inp.dataset.done = "1"; inp.blur(); renderSyncDevices(); } };
  inp.onblur = () => bmRenameCommit(inp);
  nameEl.replaceWith(inp);
  inp.focus(); inp.select();
};
async function bmRenameCommit(inp) {
  if (inp.dataset.done) return; inp.dataset.done = "1";   // Enter then blur → commit once
  const v = (inp.value || "").trim();
  if (!v) { inp.blur(); renderSyncDevices(); return; }   // empty → cancel (blur so the re-render isn't skipped)
  inp.disabled = true;   // also drops focus so renderSyncDevices isn't skipped
  let failed = false;
  try {
    const r = await (await fetch("/api/pro/rename", { method: "POST", headers: _proHdr, body: JSON.stringify({ label: v }) })).json();
    if (!r.ok) failed = true;
  } catch (e) { failed = true; }
  await renderSyncDevices();   // server re-pushed with the new label → refresh list + scope
  if (failed) {   // label IS saved locally (server persists before push) → auto-pushes within ~5 min
    const m = $("pro-msg"); if (m) { m.style.color = "var(--warn)"; m.textContent = "rename saved on this device — will sync when back online"; }
  }
}

async function proAuth(mode) {
  const email = ($("pro-email").value || "").trim(), password = $("pro-pass").value;
  const msg = $("pro-msg"), su = $("pro-signup"), li = $("pro-login");
  if (!email || !password) { msg.style.color = "var(--warn)"; msg.textContent = "email and password required"; return; }
  if (su) su.disabled = true; if (li) li.disabled = true;
  msg.style.color = ""; msg.textContent = mode === "signup" ? "creating account…" : "signing in…";
  try {
    const d = await (await fetch("/api/pro/connect", { method: "POST", headers: _proHdr,
      body: JSON.stringify({ mode, email, password }) })).json();
    if (!d.ok) { msg.style.color = "var(--warn)"; msg.textContent = d.error || "failed"; if (su) su.disabled = false; if (li) li.disabled = false; return; }
    localStorage.setItem("bm_pro_email", email);
    renderSyncDevices();   // → verify state (signup / unverified) or devices (verified)
  } catch (e) { msg.style.color = "var(--warn)"; msg.textContent = String(e); if (su) su.disabled = false; if (li) li.disabled = false; }
}

async function proResend() {
  const msg = $("pro-msg");
  try {
    const d = await (await fetch("/api/pro/resend", { method: "POST", headers: _proHdr })).json();
    if (msg) { msg.style.color = d.ok ? "var(--good)" : "var(--warn)"; msg.textContent = d.ok ? "✓ verification email re-sent" : (d.error || "failed"); }
  } catch (e) { if (msg) { msg.style.color = "var(--warn)"; msg.textContent = String(e); } }
}

async function proSignout() {
  try { await fetch("/api/pro/disconnect", { method: "POST", headers: _proHdr }); } catch (e) {}
  renderSyncDevices();
}
window.proAuth = proAuth; window.proResend = proResend; window.proSignout = proSignout;
window.renderSyncDevices = renderSyncDevices;

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
// Cross-device project rollup. scope "this"/no-devices → null (use local rep.by_project).
// scope "all" → sum each project's cost/tokens across every device's snapshot for the
// active source(s), counting how many machines run it. scope <device> → that device.
function bmScopedProjects(localRep) {
  const scope = window.__scope || "all";
  if (scope === "this" || (localRep && localRep._combined)) return null;
  const payload = window.__devicesCache;
  const devices = (payload && payload.devices ? payload.devices.filter((d) => !d._undecryptable) : []);
  if (!devices.length) return null;
  const pick = scope === "all" ? devices : devices.filter((d) => d.device_id === scope);
  if (!pick.length) return null;
  const sources = bmActiveSources();
  const agg = new Map();   // label → {label, cost, tokens, msgs, devSet, dirs}
  pick.forEach((d) => sources.forEach((s) => {
    const sp = (d.sources && d.sources[s] && d.sources[s].by_project) || [];
    sp.forEach((p) => {
      const key = p.project || "?";
      const e = agg.get(key) || { label: key, cost: 0, tokens: 0, msgs: 0, devSet: new Set(), dirs: new Set() };
      e.cost += p.cost || 0; e.tokens += p.tokens || 0; e.msgs += p.msgs || 0; e.devSet.add(d.device_id);
      if (p.project_dir) e.dirs.add(p.project_dir);
      agg.set(key, e);
    });
  }));
  return [...agg.values()]
    // Show the path only when it's unambiguous (one machine / same path); across devices
    // the same label can be different folders → fall back to just the name.
    .map((e) => ({ label: e.label, cost: e.cost, tokens: e.tokens, msgs: e.msgs, devices: e.devSet.size,
                   project_dir: e.dirs.size === 1 ? [...e.dirs][0] : null }))
    .sort((a, b) => b.cost - a.cost).slice(0, 12);
}
function renderProjects(rep) {
  const tb = document.querySelector("#projects-tbl tbody"); if (!tb) return;
  const titleEl = $("projects-title");
  const scoped = bmScopedProjects(rep);
  if (scoped) {   // fleet / single-device rollup from device snapshots
    if (titleEl) {
      const n = (window.__devicesCache && window.__devicesCache.devices || []).filter((d) => !d._undecryptable).length;
      titleEl.innerHTML = window.__scope === "all"
        ? `Projects · across ${n} devices <span class="sub">where your fleet spends</span>`
        : `Projects · this device <span class="sub">where your spend goes</span>`;
    }
    if (!scoped.length) { tb.innerHTML = `<tr><td colspan="3" class="empty">no project data yet — devices sync within ~5 min</td></tr>`; return; }
    const max = Math.max(...scoped.map((p) => p.cost), 1);
    tb.innerHTML = scoped.map((p) =>
      `<tr><td><div class="proj-cell">${bmProjCell(p.project_dir, p.label)}${p.devices > 1 ? ` <span class="proj-dev">${p.devices} devices</span>` : ""}</div><div class="cell-bar"><i style="width:${p.cost / max * 100}%"></i></div></td>
        <td class="num">${fmtCompact(p.tokens || 0)}</td>
        <td class="num">${fmtMoney0(p.cost)}</td></tr>`).join("");
    return;
  }
  // this-device (local report) — original behavior
  if (titleEl) titleEl.innerHTML = `Projects · lifetime <span class="sub">where your spend goes</span>`;
  const ps = (rep.by_project || []).slice(0, 12);
  const max = Math.max(...ps.map((p) => p.cost_usd || 0), 1);
  if (!ps.length) { tb.innerHTML = `<tr><td colspan="3" class="empty">No data</td></tr>`; return; }
  tb.innerHTML = ps.map((p) =>
    `<tr><td>${bmProjCell(p.project_dir, p.project_label)}<div class="cell-bar"><i style="width:${(p.cost_usd || 0) / max * 100}%"></i></div></td>
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
      <td>${bmProjCell(t.project_dir, t.project_label, t.device, t.chat)}</td>
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
  const localTxt = s < 2 ? "now" : s < 60 ? s + "s ago" : Math.floor(s / 60) + "m ago";
  // For a fleet/remote scope, the headline data is as-of the oldest remote snapshot — say so
  // instead of letting it read as live "now".
  let txt = localTxt;
  if (window.__scopedAsOf) {
    const ra = Math.floor((Date.now() - new Date(window.__scopedAsOf).getTime()) / 1000);
    if (isFinite(ra) && ra >= 0) txt = "synced " + (ra < 60 ? ra + "s" : Math.floor(ra / 60) + "m") + " ago";
  }
  $("last-updated").textContent = txt;
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
              "active-model": 3, daily: 5, projects: 5, recent: 6, heatmap: 3, "model-table": 5, behavior: 4, tools: 4,
              budget: 3, "sync-devices": 5 };
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
    const saved = JSON.parse(localStorage.getItem("burnmeter_layout_v8") || "null");
    // kaydet/yükle YALNIZCA tam grid'de (12 kolon). Dar ekranda GridStack otomatik reflow
    // yapar; o geçici dar düzeni kaydetmeyiz ki geniş ekran düzenini bozmasın.
    if (saved && saved.length && grid.getColumn() === 12) grid.load(saved, false);
  } catch (e) { /* bozuk kayıt → varsayılan */ }
  const save = () => { try { if (grid.getColumn() === 12) localStorage.setItem("burnmeter_layout_v8", JSON.stringify(grid.save(false))); } catch (e) {} };
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
  const sync = () => { const on = localStorage.getItem("burnmeter_alerts_on") === "1"; b.classList.toggle("on", on); b.title = on ? "limit alerts ON" : "limit alerts off (click)"; if (b.classList.contains("set-btn")) b.textContent = on ? "🔔 Alerts on" : "🔔 Alerts off"; };
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

// ---------- section navigation (sidebar menu) ----------
window.bmShowSection = function (name) {
  const valid = ["overview", "devices", "projects", "trends", "alerts", "settings"];
  name = valid.includes(name) ? name : "overview";
  // In the "both" combined view <main> is hidden, so a section click would be a silent
  // no-op. Switch back to the last single source first → render() shows <main> again.
  if (window.__source === "both") {
    const single = localStorage.getItem("burnmeter_source_single") || "claude";
    const btn = document.querySelector(`#source-toggle button[data-source="${single}"]`);
    if (btn) btn.click();
  }
  document.querySelectorAll("main > .section").forEach((s) =>
    s.classList.toggle("active", s.dataset.section === name));
  document.querySelectorAll(".nav-item[data-section]").forEach((b) =>
    b.classList.toggle("active", b.dataset.section === name));
  try { localStorage.setItem("burnmeter_section", name); } catch (e) {}
  // Charts created while their section was display:none render at 0×0 — resize on show.
  requestAnimationFrame(() => {
    Object.values(window.__charts || {}).forEach((ch) => { try { ch.resize(); } catch (e) {} });
  });
};

function start() {
  applyZoom();
  // sidebar section navigation
  document.querySelectorAll(".nav-item[data-section]").forEach((b) =>
    b.addEventListener("click", () => bmShowSection(b.dataset.section)));
  bmShowSection(localStorage.getItem("burnmeter_section") || "overview");
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
      if (src !== "both") localStorage.setItem("burnmeter_source_single", src);   // remembered for exiting "both"
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
      if (window.__lastReport) renderSpeedometer(bmScopedRep(window.__lastReport) || window.__lastReport, window.__source === "codex");
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
      if (window.__lastReport) renderActiveModel(bmScopedRep(window.__lastReport) || window.__lastReport);
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
  if (rl) rl.addEventListener("click", () => { localStorage.removeItem("burnmeter_layout_v8"); location.reload(); });
  // First paint is cold until the local logs are parsed — on large histories that
  // first read can take a few seconds. Say so instead of showing a blank "…" gauge.
  $("last-updated").textContent = "Reading your Burnmeter logs — the first run can take a minute or two on large histories…";
  refresh(false);
  setInterval(() => refresh(false), 10000);
  setInterval(updateLiveStamp, 1000);
  setInterval(tickLive, 1000);   // canlı aktif model: saniyelik nabız/şimdi güncellemesi
  setInterval(liveGaugeTick, 5000);   // burn-rate gauge decays live (→ $0 when idle)
  // Fleet view (online/idle dots, "ago", breakdown) is time-based; refresh it on its
  // own cadence so it doesn't freeze while the local machine is idle. renderSyncDevices
  // guards against clobbering an in-progress rename; /api/sync/devices is 20s-cached.
  setInterval(() => { if (window.__lastReport) renderSyncDevices(); }, 30000);   // refresh fleet (incl. "both" mode)
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
