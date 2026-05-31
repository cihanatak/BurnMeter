// ccmeter / codex_meter — dashboard frontend (modern rebuild 2026-05-30)
// Local /api/report (source=claude|codex). Numbers are the hero.

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
  const g = raw.toLowerCase().match(/gpt-?(\d+(?:\.\d+)?)(-?(codex|mini))?/);
  if (g) return "GPT-" + g[1] + (g[3] === "codex" ? " Codex" : g[3] === "mini" ? " mini" : "");
  return raw;
}
const sevClass = (pct) => pct >= 90 ? "bad" : pct >= 70 ? "warn" : "good";

// ---------- data ----------
window.__source = localStorage.getItem("ccmeter_source") || "claude";
window.__charts = {};

async function loadReport(force) {
  const p = new URLSearchParams();
  if (force) p.set("refresh", "1");
  if (window.__source !== "claude") p.set("source", window.__source);
  const qs = p.toString();
  const r = await fetch("/api/report" + (qs ? "?" + qs : ""));
  if (!r.ok) throw new Error("fetch " + r.status);
  return r.json();
}

async function refresh(force = false) {
  if (window.__refreshInFlight && !force) return;
  window.__refreshInFlight = true;
  try {
    const rep = await loadReport(force);
    if ((rep._meta?.source || "claude") !== window.__source) return; // stale
    window.__lastReport = rep;
    render(rep);
    window.__lastRefreshMs = Date.now();
  } catch (e) {
    console.error(e);
    $("last-updated").textContent = "yüklenemedi: " + e.message;
  } finally {
    window.__refreshInFlight = false;
  }
}

// ---------- render orchestrator ----------
function render(rep) {
  const src = rep._meta?.source || "claude";
  const isCodex = src === "codex";
  document.documentElement.style.setProperty("--brand", isCodex ? "var(--brand-codex)" : "var(--brand-claude)");
  document.documentElement.style.setProperty("--brand-soft", isCodex ? "var(--brand-codex-soft)" : "var(--brand-claude-soft)");
  $("app-name").textContent = isCodex ? "codex_meter" : "ccmeter";
  $("brand-logo").textContent = isCodex ? "⌬" : "◔";
  document.title = (isCodex ? "codex_meter" : "ccmeter") + " — AI coding yakıt metresi";
  $("version").textContent = "v" + (rep._meta?.version || "?");
  $("data-source").textContent = `${rep._meta?.files_scanned ?? "?"} dosya · ${fmtInt(rep.record_count || 0)} kayıt`;

  renderSpeedometer(rep, isCodex);
  renderHeroAside(rep, isCodex);
  renderKPIs(rep, isCodex);
  renderEfficiency(rep, isCodex);
  renderCache(rep);
  renderTrend(rep, isCodex);
  renderModels(rep);
  renderDaily(rep);
  renderProjects(rep);
  renderRecent(rep);
  renderModelTable(rep);
  renderBehavior(rep);
  renderTools(rep);
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
  const fc = rep.forecast || {};
  const z = rep.burn_rate_zones || { typical: 4, busy: 12, heavy: 25, max: 50 };
  const hoursStr = localStorage.getItem("ccmeter_burn_hours") || "2";
  const burnByHours = fc.burn_rates_by_hours || {};
  const rate = burnByHours[hoursStr] ?? fc.burn_rate_per_hour_recent ?? 0;
  const cx = 200, cy = 200, r = 150, max = z.max || 50;

  const arcs =
    `<path class="speedo-zone-green"  d="${arcPath(cx,cy,r,0,z.typical,max)}" stroke-width="26" fill="none"/>` +
    `<path class="speedo-zone-yellow" d="${arcPath(cx,cy,r,z.typical,z.busy,max)}" stroke-width="26" fill="none"/>` +
    `<path class="speedo-zone-orange" d="${arcPath(cx,cy,r,z.busy,z.heavy,max)}" stroke-width="26" fill="none"/>` +
    `<path class="speedo-zone-red"    d="${arcPath(cx,cy,r,z.heavy,max,max)}" stroke-width="26" fill="none"/>`;
  const zoneDefault = !!z.insufficient_history;
  const ticks = zoneDefault
    ? [{ v: 0, l: "$0" }, { v: z.typical, l: "tipik (varsayılan)" }, { v: z.busy, l: "yoğun (varsayılan)" }, { v: z.heavy, l: "ağır" }]
    : [{ v: 0, l: "$0" }, { v: z.typical, l: "senin tipik" }, { v: z.busy, l: "senin yoğun (P90)" }, { v: z.heavy, l: "çok yoğun" }];
  const ticksSvg = ticks.map(t => {
    const a = 180 - (Math.min(t.v, max) / max) * 180, p = polarToCartesian(cx, cy, r + 20, a);
    const anc = a < 90 ? "start" : a > 90 ? "end" : "middle";
    return `<text class="speedo-tick-label" x="${p.x.toFixed(1)}" y="${p.y.toFixed(1)}" text-anchor="${anc}" dominant-baseline="middle">${t.l}</text>`;
  }).join("");
  const clamped = Math.max(0, Math.min(rate, max));
  const na = 180 - (clamped / max) * 180;
  const tip = polarToCartesian(cx, cy, r - 6, na), tail = polarToCartesian(cx, cy, -24, na);

  let zc, zw;
  if (rate >= z.heavy)        { zc = "bad";  zw = "🔴 ağır bölge · çok yoğun yakıyorsun"; }
  else if (rate >= z.busy)    { zc = "warn"; zw = "🟠 yoğun bölge"; }
  else if (rate >= z.typical) { zc = "warn"; zw = "🟡 normal üstü"; }
  else                         { zc = "good"; zw = "🟢 sakin · verimli"; }
  const needleCol = zc === "bad" ? "#F2555A" : zc === "warn" ? "#F5A623" : "#2BD96B";

  const svg = $("speedo-svg");
  if (svg) svg.innerHTML = `${arcs}${ticksSvg}
    <line x1="${tail.x.toFixed(1)}" y1="${tail.y.toFixed(1)}" x2="${tip.x.toFixed(1)}" y2="${tip.y.toFixed(1)}" stroke="${needleCol}" stroke-width="4" stroke-linecap="round"/>
    <circle class="speedo-hub" cx="${cx}" cy="${cy}" r="8"/>
    <text class="speedo-value" x="${cx}" y="${cy + 36}">${fmtMoney(rate)}</text>
    <text class="speedo-unit" x="${cx}" y="${cy + 52}">/saat · son ${hoursStr === "0.25" ? "15dk" : hoursStr + "h"} ort.${isCodex ? " (notional)" : " (tahmini $)"}</text>`;

  const cw = rep.current_window || {};
  const ratio = cw.vs_baseline_ratio;
  $("speedo-zone").className = "speedo-zone-txt " + zc;
  const zoneEl = $("speedo-zone");
  zoneEl.textContent = zw;
  if (ratio != null && ratio > 0) {
    const span = document.createElement("span");
    span.append(" · normalinin ");
    const strong = document.createElement("strong");
    strong.textContent = ratio.toFixed(1) + "x";
    span.append(strong, " hızı");
    zoneEl.appendChild(span);
  } else if (rate < z.typical) {
    zoneEl.append(" · normalin altında");
  }

  // efficiency headline (fuel_efficiency) — unit economics narrative
  const fe = rep.fuel_efficiency?.headline || {};
  const zoneNote = zoneDefault
    ? `<div class="dim" style="font-size:10.5px;margin-top:5px;color:var(--text-4)">Eşikler varsayılan — henüz kişisel hız geçmişin yok.</div>`
    : `<div class="dim" style="font-size:10.5px;margin-top:5px;color:var(--text-4)">Eşikler senin geçmiş 5s pencerelerinden (P50/P90).</div>`;
  $("hero-detail").innerHTML = `<strong>${esc(fe.label || "")}</strong>${fe.detail ? " — " + esc(fe.detail) : ""}${zoneNote}`;

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
  const tf = localStorage.getItem("ccmeter_trend_tf") || "24";
  const maList = JSON.parse(localStorage.getItem("ccmeter_trend_ma") || "[7]");
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
  const H = { hero: 5, "hero-aside": 5, kpi: 2, eff: 5, cache: 5, trend: 5, models: 5,
              daily: 4, projects: 5, recent: 5, "model-table": 5, behavior: 3, tools: 3 };
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
    const saved = JSON.parse(localStorage.getItem("ccmeter_layout_v1") || "null");
    // kaydet/yükle YALNIZCA tam grid'de (12 kolon). Dar ekranda GridStack otomatik reflow
    // yapar; o geçici dar düzeni kaydetmeyiz ki geniş ekran düzenini bozmasın.
    if (saved && saved.length && grid.getColumn() === 12) grid.load(saved, false);
  } catch (e) { /* bozuk kayıt → varsayılan */ }
  const save = () => { try { if (grid.getColumn() === 12) localStorage.setItem("ccmeter_layout_v1", JSON.stringify(grid.save(false))); } catch (e) {} };
  grid.on("change", save);
  // resize sonrası grafikleri yeni boyuta sığdır
  grid.on("resizestop", () => { Object.values(window.__charts || {}).forEach(ch => { try { ch.resize(); } catch (e) {} }); });
}

function start() {
  $("refresh").addEventListener("click", () => refresh(true));
  // source toggle
  document.querySelectorAll("#source-toggle button").forEach(b => {
    b.classList.toggle("active", b.dataset.source === window.__source);
    b.addEventListener("click", () => {
      const src = b.dataset.source;
      if (src === window.__source) return;
      window.__source = src; localStorage.setItem("ccmeter_source", src);
      document.querySelectorAll("#source-toggle button").forEach(x => x.classList.toggle("active", x.dataset.source === src));
      $("last-updated").textContent = (src === "codex" ? "codex_meter" : "ccmeter") + " yükleniyor…";
      window.__refreshInFlight = false; refresh(false);
    });
  });
  // burn-window picker (speedometer ortalama penceresi: 15dk/1h/2h/4h/6h)
  const bh0 = localStorage.getItem("ccmeter_burn_hours") || "2";
  document.querySelectorAll("#burn-window-picker button").forEach(b => {
    b.classList.toggle("active", b.dataset.h === bh0);
    b.addEventListener("click", () => {
      localStorage.setItem("ccmeter_burn_hours", b.dataset.h);
      document.querySelectorAll("#burn-window-picker button").forEach(x => x.classList.toggle("active", x.dataset.h === b.dataset.h));
      if (window.__lastReport) renderSpeedometer(window.__lastReport, window.__source === "codex");
    });
  });
  // trend timeframe picker (1h/4h/6h/12h/1d)
  const tf0 = localStorage.getItem("ccmeter_trend_tf") || "24";
  document.querySelectorAll("#trend-picker button").forEach(b => {
    b.classList.toggle("active", b.dataset.tf === tf0);
    b.addEventListener("click", () => {
      localStorage.setItem("ccmeter_trend_tf", b.dataset.tf);
      document.querySelectorAll("#trend-picker button").forEach(x => x.classList.toggle("active", x.dataset.tf === b.dataset.tf));
      if (window.__lastReport) renderTrend(window.__lastReport, window.__source === "codex");
    });
  });
  // trend MA toggle buttons (7/25/50/100, multi-select)
  let maSel = JSON.parse(localStorage.getItem("ccmeter_trend_ma") || "[7]");
  document.querySelectorAll(".indicator-btn").forEach(b => {
    const n = parseInt(b.dataset.ma, 10);
    b.classList.toggle("active", maSel.includes(n));
    b.addEventListener("click", () => {
      maSel = JSON.parse(localStorage.getItem("ccmeter_trend_ma") || "[7]");
      if (maSel.includes(n)) maSel = maSel.filter(x => x !== n); else maSel.push(n);
      localStorage.setItem("ccmeter_trend_ma", JSON.stringify(maSel));
      b.classList.toggle("active", maSel.includes(n));
      if (window.__lastReport) renderTrend(window.__lastReport, window.__source === "codex");
    });
  });
  initModularGrid();
  const rl = $("reset-layout");
  if (rl) rl.addEventListener("click", () => { localStorage.removeItem("ccmeter_layout_v1"); location.reload(); });
  refresh(false);
  setInterval(() => refresh(false), 10000);
  setInterval(updateLiveStamp, 1000);
}
document.addEventListener("DOMContentLoaded", start);
