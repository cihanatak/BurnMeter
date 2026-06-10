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
  if (diff < 0) return "az sonra";
  if (diff < 60) return diff + "sn önce";
  if (diff < 3600) return Math.floor(diff / 60) + "dk önce";
  if (diff < 86400) return Math.floor(diff / 3600) + "sa önce";
  const d = Math.floor(diff / 86400);
  if (d < 7) return d + "g önce";
  return new Date(iso).toISOString().slice(0, 10);
}
function resetIn(epochSec) {
  if (!epochSec) return "";
  const rem = Math.max(0, epochSec - Date.now() / 1000);
  const h = Math.floor(rem / 3600), m = Math.floor((rem % 3600) / 60);
  return h >= 1 ? `${h}sa ${m}dk` : `${m}dk`;
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
      window.__lastRefreshMs = Date.now();
      return;
    }
    const rep = await loadReport(force);
    if ((rep._meta?.source || "claude") !== window.__source) return; // stale
    window.__reportCache[rep._meta?.source || "claude"] = rep;
    window.__lastReport = rep;
    const sig = repSig(rep);
    if (sig !== window.__renderSig) { render(rep); window.__renderSig = sig; }
    window.__lastRefreshMs = Date.now();
  } catch (e) {
    console.error(e);
    $("last-updated").textContent = "yüklenemedi: " + e.message;
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
function render(rep) {
  { const cv = $("combined-view"); if (cv) cv.style.display = "none";
    const mn = document.querySelector("main"); if (mn) mn.style.display = ""; }
  const src = rep._meta?.source || "claude";
  const isCodex = src === "codex";
  document.documentElement.style.setProperty("--brand", isCodex ? "var(--brand-codex)" : "var(--brand-claude)");
  document.documentElement.style.setProperty("--brand-soft", isCodex ? "var(--brand-codex-soft)" : "var(--brand-claude-soft)");
  $("app-name").textContent = "Burnmeter";
  $("brand-logo").textContent = isCodex ? "⌬" : "◔";
  document.title = "Burnmeter — AI coding yakıt metresi";
  $("version").textContent = "v" + (rep._meta?.version || "?");
  $("data-source").textContent = `${rep._meta?.files_scanned ?? "?"} dosya · ${fmtInt(rep.record_count || 0)} kayıt`;

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

// ---------- live ACTIVE MODEL (canlı çalışan model · 15dk/1sa/5sa) ----------
// Eski sürümde çok sevilen kutu: o an hangi model(ler) çalışıyor, ne hızla.
// Veri rep.live_active_models_by_window[15|60|300|1440] — her model: tokens_per_min,
// messages, cost_per_hour, seconds_since_last (canlı mı). En yakın = "şu an çalışan".
function renderActiveModel(rep) {
  const body = $("active-model-body"); if (!body) return;
  let w = localStorage.getItem("burnmeter_active_window2") || "5"; if (!["1", "5", "15"].includes(w)) w = "5";
  const lam = (rep.live_active_models_by_window || {})[w] || {};
  let models = (lam.models || []).filter(m => !String(m.model_id).startsWith("<"));
  const wl = w === "60" ? "1 saatte" : w === "300" ? "5 saatte" : w === "1440" ? "1 günde" : w + "dk'da";
  if (!models.length) { body.innerHTML = `<div class="dim" style="padding:16px 2px">Son ${wl} aktif model yok.</div>`; return; }
  models = models.slice().sort((a, b) => (a.seconds_since_last ?? 9e9) - (b.seconds_since_last ?? 9e9));
  body.innerHTML = models.map((m, i) => {
    const s = m.seconds_since_last ?? 9e9, live = s < 120;
    const seen = live ? "● şimdi" : s < 3600 ? `${Math.round(s / 60)}dk önce` : `${(s / 3600).toFixed(1)}sa önce`;
    return `<div class="am-row${i === 0 ? " am-top" : ""}">
      <span class="am-dot${live ? " live" : ""}"></span>
      <span class="am-name ${modelToFamily(m.model_id)}"><span class="am-modelname">${esc(modelDisplay(m.model_id))}</span><span class="badge ${m.device || "mac"}">${m.device || "mac"}</span></span>
      <span class="am-tpm num">${fmtInt(m.tokens_per_min || 0)}<span class="dim"> tok/dk</span></span>
      <span class="am-msg dim">${m.messages || 0} msg</span>
      <span class="am-rate num">${fmtMoney(m.cost_per_hour || 0)}<span class="dim">/sa</span></span>
      <span class="am-seen dim">${seen}</span>
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
  $("data-source").textContent = `Claude ${fmtInt(cl.record_count || 0)} · Codex ${fmtInt(cx.record_count || 0)} kayıt`;
  $("last-updated").textContent = "şimdi";
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
}

// ===== gauge TEK KAYNAK (single tab renderSpeedometer + İkisi paintBurnGauge ortak) =====
// Hangi gauge: CODEX rate-limit verisi varsa → binding-constraint rate-limit % (duvar);
// yoksa (Claude veya limitsiz) → burn-rate $/saat. İki yer de bunu çağırır → ASLA ayrışmaz.
function computeGaugeSpec(rep, isCodex, hoursStr) {
  const fc = rep.forecast || {};
  const rate = (fc.burn_rates_by_hours || {})[hoursStr] ?? fc.burn_rate_per_hour_recent ?? 0;
  // Hem Claude HEM Codex → AYNI burn-rate $/saat göstergesi (tutarlılık — kullanıcı
  // talebi). Codex'in rate-limit %'si hero'da DEĞİL; yan kutuda ("Plan limiti · duvar")
  // gösterilir (renderHeroAside) → bilgi kaybolmaz, ama hero her iki araçta da burn.
  const z = rep.burn_rate_zones || { typical: 4, busy: 12, heavy: 25, max: 50 };
  const zd = !!z.insufficient_history;
  const g = {
    value: rate, max: z.max || 50, zones: z,
    ticks: zd
      ? [{ v: 0, l: "$0" }, { v: z.typical, l: "tipik (varsayılan)" }, { v: z.busy, l: "yoğun (varsayılan)" }, { v: z.heavy, l: "ağır" }]
      : [{ v: 0, l: "$0" }, { v: z.typical, l: "senin tipik" }, { v: z.busy, l: "senin yoğun (P90)" }, { v: z.heavy, l: "çok yoğun" }],
    valueText: fmtMoney(rate),
    unitText: `/saat · son ${hoursStr === "0.0833" ? "5dk" : hoursStr === "0.25" ? "15dk" : hoursStr + "h"} ort.${isCodex ? " (notional)" : " (tahmini $)"}`,
    zone: rate >= z.heavy ? ["bad", "🔴 ağır bölge · çok yoğun yakıyorsun"]
        : rate >= z.busy ? ["warn", "🟠 yoğun bölge"]
        : rate >= z.typical ? ["warn", "🟡 normal üstü"]
        :                  ["good", "🟢 sakin · verimli"],
    ratio: rep.current_window?.vs_baseline_ratio, below: rate < z.typical,
    note: zd ? "Eşikler varsayılan — henüz kişisel hız geçmişin yok."
             : "Eşikler senin geçmiş 5s pencerelerinden (P50/P90).",
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
  if (els.title) els.title.textContent = isLimit ? "Limit · duvara ne kadar var?" : "Burn rate · çok mu yakıyorsun?";
  if (els.zone) {
    els.zone.className = "cv-zone2 " + g.zone[0];
    let txt = g.zone[1];
    if (g.ratio != null && g.ratio > 0) txt += ` · normalinin ${g.ratio.toFixed(1)}x`;
    else if (g.below) txt += " · normalin altında";
    els.zone.textContent = txt;
  }
  if (els.breakdown) {
    const bd = (fc.burn_rates_by_hours_by_model || {})[hoursStr] || [];
    const vis = bd.filter(m => !String(m.model_id).startsWith("<")).slice(0, 4);
    els.breakdown.innerHTML = vis.length ? vis.map(m =>
      `<span class="sb-row ${modelToFamily(m.model_id)}"><span class="sb-name">${esc(modelDisplay(m.model_id))}</span><span class="sb-val">${fmtMoney(m.cost_per_hour)}/sa</span><span class="sb-share">%${(m.share * 100).toFixed(0)}</span></span>`).join("") : "";
  }
}

// bir yarının iskeleti: head + GERÇEK speedometer (svg + window picker) + canlı model + son işler
function halfStructure(rep, isCodex, side) {
  const h = localStorage.getItem("burnmeter_burn_hours_" + side) || "2";
  const picker = [["0.0833", "5dk"], ["0.25", "15dk"], ["1", "1h"], ["2", "2h"], ["4", "4h"], ["6", "6h"]]
    .map(([v, l]) => `<button data-h="${v}"${v === h ? ' class="active"' : ""}>${l}</button>`).join("");
  const ago = (iso) => { const s = (Date.now() - new Date(iso).getTime()) / 1000; return s < 60 ? "şimdi" : s < 3600 ? Math.round(s / 60) + "dk" : s < 86400 ? Math.round(s / 3600) + "sa" : Math.round(s / 86400) + "g"; };
  const lam = (rep.live_active_models_by_window || {})["5"] || {};
  const models = (lam.models || []).filter(m => !String(m.model_id).startsWith("<"))
    .sort((a, b) => (a.seconds_since_last ?? 9e9) - (b.seconds_since_last ?? 9e9)).slice(0, 4);
  const amRows = models.length ? models.map(m => {
    const s = m.seconds_since_last ?? 9e9, live = s < 120;
    const dev = m.device || "mac";
    return `<div class="am-row"><span class="am-dot${live ? " live" : ""}"></span><span class="am-name ${modelToFamily(m.model_id)}"><span class="am-modelname">${esc(modelDisplay(m.model_id))}</span><span class="badge ${dev}">${dev}</span></span><span class="am-tpm num">${fmtInt(m.tokens_per_min || 0)}<span class="dim"> tok/dk</span></span><span class="am-seen dim">${live ? "● şimdi" : Math.round(s / 60) + "dk"}</span></div>`;
  }).join("") : `<div class="dim" style="padding:6px 0">son 5dk aktif model yok</div>`;
  const recent = (rep.recent_turns || []).filter(t => !String(t.model || "").startsWith("<")).slice(0, 6);
  const recRows = recent.length ? recent.map(t => `<div class="cvr-row"><span class="cvr-when dim">${ago(t.timestamp)}</span><span class="cvr-proj"><span class="cvr-projname">${esc(t.project_label || "?")}</span><span class="badge ${t.device || "mac"}">${t.device || "mac"}</span></span><span class="cvr-model ${modelToFamily(t.model)}">${esc(modelDisplay(t.model))}</span><span class="cvr-tok num">${fmtInt(t.total_tokens || 0)}</span></div>`).join("") : `<div class="dim" style="padding:6px 0">aktivite yok</div>`;
  return `<div class="cv-head"><span class="cv-logo">${isCodex ? "⌬" : "◔"}</span><span class="cv-title">${isCodex ? "Codex" : "Claude Code"}</span><span class="cv-sub dim">${fmtInt(rep.record_count || 0)} kayıt</span></div>
    <div class="cvb cv-herobox">
      <div class="cvb-trow"><span class="cvb-t dim">Burn rate · çok mu yakıyorsun?</span><span class="picker cv-picker">${picker}</span></div>
      <svg class="cv-speedo" viewBox="0 0 400 272" preserveAspectRatio="xMidYMid meet"></svg>
      <div class="cv-zone2"></div>
      <div class="cv-breakdown speedo-breakdown"></div>
    </div>
    <div class="cvb"><div class="cvb-t dim">Canlı aktif model</div>${amRows}</div>
    <div class="cvb"><div class="cvb-t dim">Son işler</div>${recRows}</div>`;
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
    span.append(" · normalinin ");
    const strong = document.createElement("strong");
    strong.textContent = g.ratio.toFixed(1) + "x";
    span.append(strong, " hızı");
    zoneEl.appendChild(span);
  } else if (g.below) {
    zoneEl.append(" · normalin altında");
  }

  // efficiency headline (fuel_efficiency) + demoted note (öbür limit / eşik kaynağı)
  const fe = rep.fuel_efficiency?.headline || {};
  const noteHtml = `<div class="dim" style="font-size:10.5px;margin-top:5px;color:var(--text-4)">${esc(g.note)}</div>`;
  $("hero-detail").innerHTML = `<strong>${esc(fe.label || "")}</strong>${fe.detail ? " — " + esc(fe.detail) : ""}${noteHtml}`;

  // per-model breakdown of this burn (which model is eating the $/hr)
  const bd = (fc.burn_rates_by_hours_by_model || {})[hoursStr] || [];
  const vis = bd.filter(m => !String(m.model_id).startsWith("<")).slice(0, 4);
  $("speedo-breakdown").innerHTML = vis.length
    ? `<div class="dim" style="font-size:10px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px">bu hızı kim yakıyor · tahmini $</div>` +
      vis.map(m => `<span class="sb-row ${modelToFamily(m.model_id)}"><span class="sb-name">${esc(modelDisplay(m.model_id))}</span>
        <span class="sb-val">${fmtMoney(m.cost_per_hour)}/sa</span><span class="sb-share">%${(m.share * 100).toFixed(0)}</span></span>`).join("")
    : "";
}

// ---------- binding-constraint aside ("duvara ne kadar var") — NET etiketli ----------
function renderHeroAside(rep, isCodex) {
  const fc = rep.forecast || {};
  const cw = rep.current_window || {};
  $("aside-title").firstChild.textContent = isCodex ? "Plan limiti · duvar " : "Lokal kullanım ";
  $("aside-sub").textContent = isCodex ? "Codex Pro" : "Claude";

  const bigPct = (pct, label, sub, resetSec) => {
    const c = sevClass(pct);
    return `<div style="margin-bottom:14px">
      <div style="display:flex;align-items:baseline;gap:8px">
        <span class="num ${c}" style="font-size:34px;font-weight:600">%${Math.round(pct)}</span>
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
    body += bigPct(wk.used_percent || 0, "haftalık Codex limiti", "bu hafta kullandığın oran", wk.resets_at);
    body += bigPct(h5.used_percent || 0, "5 saat limiti", "son 5 saatte kullandığın oran", h5.resets_at);
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
    body += bigFact("~" + fmtMoney0(used), "bu 5 saatlik blok",
      (cw.active ? "şu an açık" : "blok kapalı")
      + (rate ? ` · ~${fmtMoney(rate)}/sa` : "")
      + (p90 ? ` · yoğun bloğun ~${fmtMoney0(p90)}` : ""));
    body += bigFact("~" + fmtMoney0(wc.rolling_7d_cost || 0), "bu hafta (7 gün)",
      wc.historical_7d_p95 ? `yoğun haftan ~${fmtMoney0(wc.historical_7d_p95)}` : "lokal toplam");
    body += `<div class="dim" style="font-size:10.5px;line-height:1.45;margin:-4px 0 14px;color:var(--text-4)">Anthropic hesap-limiti/kotası vermez — bunlar sadece lokal kullanım (tahmini $).</div>`;
  }

  // key stats footer — clearly labeled
  const rows = [];
  rows.push(["Bugün", fmtMoney0(fc.today?.so_far ?? 0)]);
  if (fc.today?.projected_eod != null) rows.push(["Gün sonu tahmini", fmtMoney0(fc.today.projected_eod)]);
  if (!isCodex && cw.projected_close_cost != null) rows.push(["Bu blok ~kapanış", fmtMoney0(cw.projected_close_cost)]);
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
  const notional = isCodex ? " (notional)" : "";
  const kpis = [
    { l: "Bugün harcanan", v: "~" + fmtMoney(fc.today?.so_far ?? 0), s: `${fmtInt(todayE.messages || 0)} turn`, spark: daily.slice(-14).map(d => d.cost_usd || 0) },
    { l: "Burn rate", v: "~" + fmtMoney(burn), unit: "/sa", s: "son 2 saat tempo" + notional, spark: null },
    { l: "Önlenen maliyet (notional)", v: "~" + fmtMoney0(ce.usd_saved || 0), s: `~%${Math.round((ce.discount_pct || 0) * 100)} daha ucuz · varsayımsal · lifetime`, spark: null },
    { l: "Lifetime $", v: "~" + fmtMoney0(t.cost_usd || 0) + notional, s: `${fmtCompact(t.total_tokens || 0)} token`, spark: daily.slice(-14).map(d => d.cost_usd || 0) },
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
  const est = isCodex ? " (notional)" : " (tahmini)";
  let html = "";
  if (ph.you != null) html += row("Saat başı maliyet",
    "~" + fmtMoney(ph.you) + "/sa" + est, `senin tipik $${ph.band_normal_low}–${ph.band_normal_high}/sa aralığın · ${ph.hours_per_active_day}h/gün · aktif saat 5dk bucket'tan`,
    ph.verdict_label || "", ph.verdict_kind);
  if (pd.you_avg_recent7 != null) html += row("Gün başı maliyet",
    "~" + fmtMoney0(pd.you_avg_recent7) + est, `senin son-30g ort. ~$${pd.baseline_avg}/gün (yoğun P90 $${pd.baseline_p90})`,
    pd.verdict_label || "", pd.verdict_kind);
  if (pc.you_median != null) html += row("Commit başı maliyet",
    "~" + fmtMoney(pc.you_median) + est, `${pc.matched_commits} commit eşleşti · ortanca/commit · hesap verisi yok → kıyas yok`,
    pc.verdict_label || "", pc.verdict_kind);
  if (pt.you != null) html += row("Turn başı maliyet",
    "~" + fmtMoney(pt.you) + est, `community tahmini ~$${pt.baseline_low}–$${pt.baseline_high}/turn (yayınlanmış norm yok) · ${fmtInt(pt.turns)} turn · tüm geçmiş ort.`,
    pt.verdict_label || "", pt.verdict_kind);
  if (ch.you != null) html += row("Cache hit",
    fmtPct(ch.you), `hedef ≥%${Math.round((ch.target_excellent || .85) * 100)} mükemmel`,
    ch.verdict_label || "", ch.verdict_kind);

  $("eff-body").innerHTML = html || `<div class="empty">Yeterli veri yok</div>`;
}

// ---------- cache efficiency ----------
function renderCache(rep) {
  const ce = rep.cache_efficiency || {};
  const hit = (ce.hit_rate || 0) * 100;
  $("cache-body").innerHTML =
    `<div style="display:flex;align-items:baseline;gap:8px;margin-bottom:4px">
       <span class="num" style="font-size:34px;font-weight:600;color:var(--text-1)">%${hit.toFixed(1)}</span>
       <span class="dim" style="font-size:12px">cache hit</span></div>
     <div class="meter-track" style="margin:10px 0"><div class="meter-fill good" style="width:${Math.min(100, hit)}%"></div></div>
     <div style="display:flex;justify-content:space-between;margin-top:14px">
       <div><div class="num" style="font-size:18px;color:var(--good)">~${fmtMoney0(ce.usd_saved || 0)}</div><div class="dim" style="font-size:11px">önlenen maliyet (varsayımsal)</div></div>
       <div style="text-align:right"><div class="num" style="font-size:18px;color:var(--text-1)">~${fmtMoney0(ce.usd_on_cache || 0)}</div><div class="dim" style="font-size:11px">cache okuma maliyeti (tahmini)</div></div>
     </div>
     <div class="dim" style="font-size:11px;margin-top:12px;line-height:1.5">Cache olmasaydı (aynı kullanımla) bu okumalar ~${fmtMoney0(ce.full_equiv_usd || 0)} ederdi — kaba bir üst sınır (~%${Math.round((ce.discount_pct || 0) * 100)} daha ucuz).</div>`;
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
           ${budget > 0 ? "Aylık bütçeyi güncelle." : "Aylık bir $ bütçe belirle — bu ay ne harcadığını ve ay-sonu tahminini bütçene karşı gör."}
           <span style="color:var(--text-4)">Yalnızca bu tarayıcıda saklanır; rakamlar tahmindir.</span>
         </div>
         <div class="budget-set">
           <span class="budget-cur">$</span>
           <input id="budget-input" type="number" min="0" step="10" placeholder="200" value="${budget > 0 ? budget : ""}" inputmode="decimal" />
           <button id="budget-save">kaydet</button>
           ${budget > 0 ? `<button id="budget-cancel" class="budget-ghost">vazgeç</button><button id="budget-clear" class="budget-ghost">sil</button>` : ""}
         </div>
         <div class="dim" style="font-size:11px">${src} · bu ay şu ana dek: ~${fmtMoney0(soFar)}</div>
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
    if (inp) inp.focus();
    return;
  }

  const pct = (soFar / budget) * 100;
  const projPct = (projEom / budget) * 100;
  const over = projEom - budget;
  const cls = projEom > budget ? "bad" : pct > 90 ? "warn" : "good";
  const verdict = projEom > budget
    ? `<span style="color:var(--bad);font-weight:600">⚠ ~${fmtMoney0(over)} aşım öngörülüyor</span>`
    : `<span style="color:var(--good);font-weight:600">✓ bütçe içinde — ~${fmtMoney0(budget - projEom)} pay kalır</span>`;
  const projMark = (projPct > pct && projPct <= 100)
    ? `<span class="budget-proj" style="left:${projPct.toFixed(1)}%" title="ay sonu tahmini ~${fmtMoney0(projEom)}"></span>` : "";

  body.innerHTML =
    `<div class="budget-grid">
       <div class="budget-nums">
         <div><span class="num" style="font-size:30px;font-weight:600;color:var(--text-1)">~${fmtMoney0(soFar)}</span>
           <span class="dim" style="font-size:13px"> / ${fmtMoney0(budget)}</span></div>
         <div class="dim" style="font-size:11px;margin-top:2px">bu ay harcanan · %${pct.toFixed(0)}</div>
       </div>
       <div class="budget-barwrap">
         <div class="meter-track" style="height:12px;position:relative;overflow:visible">
           <div class="meter-fill ${cls}" style="width:${Math.min(100, pct).toFixed(1)}%"></div>
           ${projMark}
         </div>
         <div style="display:flex;justify-content:space-between;font-size:11px;margin-top:7px" class="dim">
           <span>$0</span>
           <span>ay sonu tahmini: ~${fmtMoney0(projEom)} (%${projPct.toFixed(0)})</span>
           <span>${fmtMoney0(budget)}</span>
         </div>
       </div>
       <div class="budget-verdict">
         ${verdict}
         <div class="dim" style="font-size:11px;margin-top:4px">kalan ${daysLeft}g · tipik ~${fmtMoney(dailyRate)}/gün · $ tahmindir</div>
         <button id="budget-edit" class="budget-ghost" style="margin-top:9px">✎ bütçeyi düzenle</button>
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
      `<div class="dim" style="font-size:12.5px;line-height:1.55;max-width:620px">
         <span style="color:var(--text-3)">Pro cross-device sync kapalı.</span> Başka makinelerinin
         kullanımını burada gör — uçtan-uca şifreli, relay yalnızca ciphertext görür, ham log gitmez.
         <div style="margin-top:9px;font:500 11px/1.5 var(--font-mono);color:var(--text-4)">burnmeter sync login --relay &lt;url&gt; --token &lt;token&gt;<br>burnmeter sync push</div>
       </div>`;
    return;
  }
  if (data.error) {
    body.innerHTML = `<div class="dim" style="font-size:12px;color:var(--warn)">relay'e ulaşılamadı: ${esc(String(data.error))}</div>`;
    return;
  }
  const devs = data.devices || [];
  if (!devs.length) {
    body.innerHTML = `<div class="dim" style="font-size:12.5px">henüz senkron cihaz yok — <span style="font-family:var(--font-mono)">burnmeter sync push</span> ile bu cihazı gönder.</div>`;
    return;
  }
  const ago = (iso) => { if (!iso) return ""; const s = (Date.now() - new Date(iso).getTime()) / 1000; return s < 60 ? "şimdi" : s < 3600 ? Math.round(s / 60) + "dk" : s < 86400 ? Math.round(s / 3600) + "sa" : Math.round(s / 86400) + "g"; };
  body.innerHTML = `<div class="sync-grid">` + devs.map(d => {
    if (d._undecryptable) {
      return `<div class="sync-dev"><div class="sync-dev-head"><span class="sync-dot bad"></span><b>${esc(d.device_id || "?")}</b></div>
        <div class="dim" style="font-size:11px">çözülemedi · passphrase farklı olabilir</div></div>`;
    }
    const me = d.device_id === data.this_device;
    const srcs = d.sources || {};
    const rows = Object.keys(srcs).map(s => {
      const v = srcs[s] || {};
      return `<div class="sync-src"><span class="sync-srcname ${s === 'codex' ? 'fam-gpt' : 'fam-opus'}">${esc(s)}</span>
        <span class="num">~${fmtMoney(v.month_so_far || 0)}</span><span class="dim">bu ay</span>
        <span class="num">${fmtInt(v.record_count || 0)}</span><span class="dim">kayıt</span></div>`;
    }).join("") || `<div class="dim" style="font-size:11px">veri yok</div>`;
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
      <div class="sync-dev-head"><span class="sync-dot live"></span><b>${esc(d.label || "?")}</b>${me ? '<span class="sync-badge">bu cihaz</span>' : ''}<span class="sync-ago dim">${ago(d._updated_at)}</span></div>
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
  $("trend-unit").textContent = "$/saat (tahmini) · " + (tf === "24" ? "1 gün" : tf + "h") + " pencere";
  const series = (rep.burn_rate_timeseries || {})[tf] || [];
  const shortWin = (tf === "1" || tf === "4" || tf === "6");
  const labels = series.map(p => {
    const dt = p.start ? new Date(p.start) : null;
    if (!dt) return "";
    return shortWin ? dt.toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit" })
                    : (dt.getMonth() + 1) + "/" + dt.getDate();
  });
  const burns = series.map(p => p.cost_per_hour ?? p.cost ?? 0);
  const datasets = [{ type: "bar", label: `$/sa`, data: burns,
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
          labels: { color: "#8A8F98", font: { size: 10 }, boxWidth: 10, padding: 8, filter: i => i.text !== "$/sa" } },
        tooltip: { ...tooltipCfg(), callbacks: { label: c => `${c.dataset.label}: ~${fmtMoney(c.parsed.y || 0)}/sa (tahmini)` } }
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
  $("daily-sub").textContent = daily.length ? `toplam ~${fmtMoney0(data.reduce((a, b) => a + b, 0))} (tahmini)` : "";
  const ctx = $("chart-daily");
  if (window.__charts.daily) window.__charts.daily.destroy();
  // Daily bars are per-DAY totals (estimated $), not an hourly rate — override
  // the shared yMoney tooltip (which appends "/sa") to label them correctly.
  const dailyOpts = chartOpts({ yMoney: true });
  dailyOpts.plugins.tooltip.callbacks = { label: (c) => fmtMoney(c.parsed.y) + " (gün toplamı · tahmini)" };
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
  if (!ps.length) { tb.innerHTML = `<tr><td colspan="3" class="empty">Veri yok</td></tr>`; return; }
  tb.innerHTML = ps.map(p =>
    `<tr><td><div>${esc(p.project_label)}</div><div class="cell-bar"><i style="width:${(p.cost_usd || 0) / max * 100}%"></i></div></td>
      <td class="num">${fmtCompact(p.total_tokens || 0)}</td>
      <td class="num">${fmtMoney0(p.cost_usd)}</td></tr>`).join("");
}

// ---------- recent turns ----------
function renderRecent(rep) {
  const isCodex = (rep._meta?.source || "claude") === "codex";
  const rsub = $("recent-sub");
  if (rsub) rsub.textContent = isCodex ? "saf net (limitten düşen)" : "maliyet ağırlıklı eşdeğer token";
  const turns = (rep.recent_turns || []).filter(t => !String(t.model || "").startsWith("<"));
  const tb = document.querySelector("#recent-tbl tbody");
  if (!turns.length) { tb.innerHTML = `<tr><td colspan="6" class="empty">Henüz aktivite yok</td></tr>`; return; }
  tb.innerHTML = turns.map(t => {
    const fam = modelToFamily(t.model);
    const dev = t.device || "mac";
    return `<tr>
      <td>${relTime(t.timestamp)}</td>
      <td><div>${esc(t.project_label)} <span class="badge ${dev}">${dev}</span></div></td>
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
  if (!grid.length || !mx) { body.innerHTML = `<div class="dim" style="padding:10px 0">aktivite verisi yok</div>`; return; }
  const days = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"];
  let h = `<div class="hm-grid"><div class="hm-corner"></div>`;
  for (let x = 0; x < 24; x++) h += `<div class="hm-hour">${x % 3 === 0 ? x : ""}</div>`;
  for (let d = 0; d < 7; d++) {
    h += `<div class="hm-day">${days[d]}</div>`;
    for (let x = 0; x < 24; x++) {
      const v = grid[d][x] || 0, it = mx ? v / mx : 0;
      const lvl = v === 0 ? 0 : it > 0.66 ? 4 : it > 0.33 ? 3 : it > 0.12 ? 2 : 1;
      h += `<div class="hm-cell lvl${lvl}" title="${days[d]} ${x}:00 · ${fmtInt(v)} token"></div>`;
    }
  }
  body.innerHTML = h + `</div>`;
}

// ---------- per-model table ----------
function renderModelTable(rep) {
  const bmf = (rep.by_model_full || []).filter(m => !String(m.model_id || "").startsWith("<")).slice(0, 10);
  const tb = document.querySelector("#modeltbl tbody");
  if (!bmf.length) { tb.innerHTML = `<tr><td colspan="4" class="empty">Veri yok</td></tr>`; return; }
  tb.innerHTML = bmf.map(m => {
    const fam = modelToFamily(m.model_id);
    return `<tr><td><span class="pill ${fam}">${modelDisplay(m.model_id)}</span></td>
      <td class="num">${fmtInt(m.messages)}</td>
      <td class="num">${fmtCompact(m.total_tokens)}</td>
      <td class="num">${fmtMoney0(m.cost_usd)}</td></tr>`;
  }).join("");
}

// ---------- behavior (errors) ----------
const ERR_LABELS = { timeout: "timeout", file_not_found: "dosya yok", permission_denied: "izin yok", command_not_found: "komut yok", python_error: "python", bash_failure: "bash exit1", stale_reference: "stale ref", output_too_large: "büyük çıktı", rate_limited: "rate limit", other_error: "diğer" };
function renderBehavior(rep) {
  const e = rep.errors || {};
  $("behavior-sub").textContent = `${e.total || 0} hata · lifetime ${fmtInt(e.total_lifetime || 0)}`;
  const cats = (e.by_category || []).slice(0, 5);
  if (!cats.length) { $("behavior-body").innerHTML = `<div style="display:flex;align-items:center;gap:8px;padding:10px 0"><span class="num" style="font-size:30px;color:var(--good)">0</span><span class="dim">son 24h temiz</span></div>`; return; }
  const max = Math.max(...cats.map(c => c.count), 1);
  $("behavior-body").innerHTML =
    `<div style="display:flex;align-items:baseline;gap:8px;margin-bottom:10px"><span class="num" style="font-size:28px;color:${e.total > 20 ? "var(--warn)" : "var(--text-1)"}">${e.total || 0}</span><span class="dim" style="font-size:12px">son 24h</span></div>` +
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
  if (!all.length) { $("tools-sub").textContent = ""; $("tools-body").innerHTML = `<div class="empty">Tool verisi yok (Codex tool isimleri parse edilmiyor)</div>`; return; }
  const rows = all.map(t => ({ tool: t.tool, calls: t.calls || 0, tok: t.approx_output_tokens || 0 })).sort((a, b) => b.tok - a.tok).slice(0, 8);
  const totalTok = all.reduce((s, t) => s + (t.approx_output_tokens || 0), 0);
  const mcp = all.filter(t => /^mcp__/.test(t.tool) || /Chrome|chrome/.test(t.tool));
  const ctok = mcp.reduce((s, t) => s + (t.approx_output_tokens || 0), 0);
  $("tools-sub").textContent = `≈${fmtCompact(totalTok)} output token` + (ctok ? ` · MCP %${(ctok / totalTok * 100).toFixed(1)} (tümü)` : "");
  const max = Math.max(...rows.map(r => r.tok), 1);
  $("tools-body").innerHTML = rows.map(r => {
    const short = r.tool.replace(/^mcp__/, "").replace(/__/g, ":").slice(0, 28);
    return `<div class="bar-list-row"><span class="nm" title="${esc(r.tool)}">${esc(short)}</span>
      <span class="tr"><i style="width:${r.tok / max * 100}%"></i></span>
      <span class="vl" title="output token tool sayısına eşit bölünerek yaklaşık hesaplandı">${fmtInt(r.calls)}× · ≈${fmtCompact(r.tok)}t</span></div>`;
  }).join("");
}

// ---------- charts shared ----------
function tooltipCfg() {
  return { backgroundColor: "#1C1D1F", borderColor: "rgba(255,255,255,0.09)", borderWidth: 1, titleColor: "#F7F8F8", bodyColor: "#D0D6E0", padding: 10, cornerRadius: 8, displayColors: false, bodyFont: { family: "'Geist Mono',monospace" } };
}
function chartOpts({ yMoney }) {
  return {
    responsive: true, maintainAspectRatio: false, animation: false,
    plugins: { legend: { display: false }, tooltip: { ...tooltipCfg(), callbacks: yMoney ? { label: (c) => fmtMoney(c.parsed.y) + "/sa" } : {} } },
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
  $("last-updated").textContent = s < 2 ? "şimdi" : s < 60 ? s + "sn önce" : Math.floor(s / 60) + "dk önce";
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
  const H = { hero: 5, "hero-aside": 5, kpi: 2, eff: 5, cache: 3, trend: 5, models: 5,
              "active-model": 4, daily: 4, projects: 5, recent: 5, heatmap: 3, "model-table": 5, behavior: 3, tools: 3,
              budget: 4, "sync-devices": 3 };
  const gs = document.createElement("div");
  gs.className = "grid-stack";
  cards.forEach(card => {
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
    const saved = JSON.parse(localStorage.getItem("burnmeter_layout_v2") || "null");
    // kaydet/yükle YALNIZCA tam grid'de (12 kolon). Dar ekranda GridStack otomatik reflow
    // yapar; o geçici dar düzeni kaydetmeyiz ki geniş ekran düzenini bozmasın.
    if (saved && saved.length && grid.getColumn() === 12) grid.load(saved, false);
  } catch (e) { /* bozuk kayıt → varsayılan */ }
  const save = () => { try { if (grid.getColumn() === 12) localStorage.setItem("burnmeter_layout_v2", JSON.stringify(grid.save(false))); } catch (e) {} };
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
    if (bp >= 95) return { level: 3, msg: `🔴 Codex limiti %${Math.round(bp)} — throttle ÇOK YAKIN` };
    if (bp >= 85) return { level: 2, msg: `🟠 Codex limiti %${Math.round(bp)} — duvara yaklaşıyorsun` };
    return { level: 0, msg: "" };
  }
  // Claude (hesap limiti yok) → aşırı burn uyarısı
  const fc = rep.forecast || {}, z = rep.burn_rate_zones || {};
  const rate = (fc.burn_rates_by_hours || {})["2"] ?? fc.burn_rate_per_hour_recent ?? 0;
  if (z.heavy && rate >= z.heavy * 1.6) return { level: 2, msg: `🟠 Burn rate çok yüksek: ${fmtMoney(rate)}/sa` };
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
  try { if (window.Notification && Notification.permission === "granted") new Notification("Burnmeter · limit uyarısı", { body: msg }); } catch (e) {}
}
function wireAlerts() {
  const b = $("alert-toggle"); if (!b) return;
  const sync = () => { const on = localStorage.getItem("burnmeter_alerts_on") === "1"; b.classList.toggle("on", on); b.title = on ? "limit uyarıları AÇIK" : "limit uyarıları kapalı (tıkla)"; };
  sync();
  b.addEventListener("click", async () => {
    const on = localStorage.getItem("burnmeter_alerts_on") === "1";
    if (!on) {
      localStorage.setItem("burnmeter_alerts_on", "1");
      try { if (window.Notification && Notification.permission === "default") await Notification.requestPermission(); } catch (e) {}
      fireAlert("🔔 Limit uyarıları açıldı — duvara %85'te haber veririm.", 2);
    } else { localStorage.setItem("burnmeter_alerts_on", "0"); }
    sync();
  });
}

function start() {
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
      if (!instant) $("last-updated").textContent = "Burnmeter yükleniyor…";
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
  let aw0 = localStorage.getItem("burnmeter_active_window2") || "5"; if (!["1", "5", "15"].includes(aw0)) aw0 = "5";
  document.querySelectorAll("#active-window-picker button").forEach(b => {
    b.classList.toggle("active", b.dataset.w === aw0);
    b.addEventListener("click", () => {
      localStorage.setItem("burnmeter_active_window2", b.dataset.w);
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
  if (rl) rl.addEventListener("click", () => { localStorage.removeItem("burnmeter_layout_v2"); location.reload(); });
  refresh(false);
  setInterval(() => refresh(false), 10000);
  setInterval(updateLiveStamp, 1000);
}
document.addEventListener("DOMContentLoaded", start);
