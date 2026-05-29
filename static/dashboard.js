// ccmeter dashboard — human-eye view. One screen, six elements.
// Backend (/api/report) provides everything; this file only renders what
// a human actually wants to glance at.

// --- Helpers ---------------------------------------------------------------

const fmtInt = (n) => (Number(n) || 0).toLocaleString("en-US");
const fmtMoney = (n) => "$" + (Number(n) || 0).toLocaleString("en-US", {
  minimumFractionDigits: 2, maximumFractionDigits: 2,
});
const fmtPct = (n) => ((Number(n) || 0) * 100).toFixed(1) + "%";

function modelToFamily(m) {
  m = (m || "").toLowerCase();
  if (m.includes("opus"))   return "opus";
  if (m.includes("sonnet")) return "sonnet";
  if (m.includes("haiku"))  return "haiku";
  // OpenAI / Codex aileleri — marka rengi (CSS .pill.gpt*).
  if (m.includes("codex"))  return "gpt-codex";
  if (m.includes("mini"))   return "gpt-mini";
  if (m.includes("gpt-5.5") || m.includes("gpt5.5")) return "gpt-55";
  if (m.includes("gpt-5.4") || m.includes("gpt5.4")) return "gpt-54";
  if (m.includes("gpt") || m.startsWith("o1") || m.startsWith("o3")) return "gpt-55";
  return "unknown";
}

// Convert raw model ID to human display.
//   claude-opus-4-7              → Opus 4.7
//   claude-sonnet-4-5-20250929   → Sonnet 4.5
//   claude-sonnet-4-6            → Sonnet 4.6
//   claude-haiku-4-5-20251001    → Haiku 4.5
//   <synthetic>                   → synthetic
function modelDisplay(m) {
  if (!m) return "—";
  const raw = String(m);
  if (raw.startsWith("<")) return raw.replace(/[<>]/g, "");
  const lower = raw.toLowerCase();
  const match = lower.match(/(opus|sonnet|haiku)-(\d+)-(\d+)/);
  if (match) {
    const family = match[1].charAt(0).toUpperCase() + match[1].slice(1);
    return `${family} ${match[2]}.${match[3]}`;
  }
  // OpenAI / Codex: gpt-5.5 → GPT-5.5, gpt-5.3-codex → GPT-5.3 Codex
  const gpt = lower.match(/gpt-?(\d+(?:\.\d+)?)(-?(codex|mini))?/);
  if (gpt) {
    let s = "GPT-" + gpt[1];
    if (gpt[3] === "codex") s += " Codex";
    else if (gpt[3] === "mini") s += " mini";
    return s;
  }
  return raw;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function relTime(iso) {
  const t = new Date(iso);
  const diffSec = Math.floor((Date.now() - t.getTime()) / 1000);
  if (diffSec < 0)        return "az sonra";
  if (diffSec < 60)       return `${diffSec}sn önce`;
  if (diffSec < 3600)     return `${Math.floor(diffSec / 60)}dk önce`;
  if (diffSec < 86400)    return `${Math.floor(diffSec / 3600)}sa önce`;
  const days = Math.floor(diffSec / 86400);
  if (days < 7)           return `${days}g önce`;
  return t.toISOString().slice(0, 16).replace("T", " ");
}

// --- Data load -------------------------------------------------------------

// Active data source: "claude" (ccmeter) or "codex" (codex_meter).
// Persisted so a reload keeps the chosen meter.
window.__source = localStorage.getItem("ccmeter_source") || "claude";

async function loadReport(force = false) {
  const params = new URLSearchParams();
  if (force) params.set("refresh", "1");
  if (window.__source && window.__source !== "claude") params.set("source", window.__source);
  const qs = params.toString();
  const url = "/api/report" + (qs ? "?" + qs : "");
  const resp = await fetch(url);
  if (!resp.ok) throw new Error("fetch failed: " + resp.status);
  return await resp.json();
}

// --- Render orchestrator ---------------------------------------------------

function render(report) {
  const src = report._meta?.source || "claude";
  document.getElementById("version").textContent = "v" + (report._meta?.version || "?");
  // Header app name reflects the active meter.
  const appNameEl = document.getElementById("app-name");
  if (appNameEl) appNameEl.textContent = (src === "codex") ? "codex_meter" : "ccmeter";
  document.title = (src === "codex" ? "codex_meter" : "ccmeter") + " — yakıt metresi";
  document.getElementById("data-source").textContent =
    `${report._meta?.files_scanned ?? "?"} files · ${(report.record_count ?? 0).toLocaleString()} records`;

  renderHero(report);
  renderCodexRateLimit(report, src);
  renderSpeedometer(report, { device: "mac" });
  renderSpeedometer(report, {
    device: "pc",
    svgId: "speedo-svg-pc",
    zoneId: "speedo-zone-pc",
    pickerId: "burn-window-picker-pc",
    lsKey: "ccmeter_burn_hours_pc",
  });
  renderTrip(report);
  renderLive(report);
  renderModels(report);
  renderBehavior(report);
  renderToolMix(report);
  renderRecent(report);
  renderProjects(report);
  renderDailyChart(report);
  renderTrendChart(report);
  renderFooter(report);
}

function trimToolName(name) {
  if (!name) return "";
  // mcp__Claude_in_Chrome__browser_batch → Chrome:browser_batch
  if (name.startsWith("mcp__")) {
    const parts = name.split("__");
    return parts.length >= 3 ? `${parts[1]}:${parts[2]}` : name;
  }
  return name;
}

function renderToolMix(report) {
  // Cihan paradigm 2026-05-29 v6: Chrome MCP (ve diğer tool'lar) her aracın
  // YAKDIĞI TOKEN'I göster — sadece kaç kez çağrıldığı değil. Sort: token desc
  // (büyük token yiyenler tepede). Label: `${calls}× · ${total_token}t · ${avg}t/c`.
  const all = (report.by_tool || []).filter(t => t.tool && t.tool !== "(no tool)");
  const rows = all
    .map(t => ({ ...t, tot: t.approx_output_tokens || 0, avg: t.calls ? Math.round((t.approx_output_tokens || 0) / t.calls) : 0 }))
    .sort((a, b) => b.tot - a.tot)
    .slice(0, 10);
  const headlineEl = document.getElementById("toolmix-headline");
  const barsEl = document.getElementById("toolmix-bars");
  if (!barsEl) return;

  if (!rows.length) {
    headlineEl.textContent = "Tool kullanımı yok.";
    barsEl.innerHTML = "";
    return;
  }

  const totalCalls = all.reduce((s, t) => s + (t.calls || 0), 0);
  const totalTokens = all.reduce((s, t) => s + (t.approx_output_tokens || 0), 0);
  const max = Math.max(...rows.map(t => t.tot)) || 1;

  // Chrome MCP araçlarının token payı (Cihan'ın asıl ilgilendiği).
  const chrome = all.filter(t => /Chrome|chrome/.test(t.tool));
  const chromeTok = chrome.reduce((s, t) => s + (t.approx_output_tokens || 0), 0);
  const chromeCalls = chrome.reduce((s, t) => s + (t.calls || 0), 0);
  const chromeShare = totalTokens > 0 ? (chromeTok / totalTokens * 100).toFixed(1) : "0";

  headlineEl.innerHTML =
    `<strong>${fmtInt(totalTokens)}</strong> tool output token (top ${rows.length}). ` +
    `Chrome MCP: ${fmtInt(chromeCalls)}× / ${fmtInt(chromeTok)}t (<strong>%${chromeShare}</strong>).`;

  barsEl.innerHTML = rows.map(t => {
    const pct = (t.tot / max * 100).toFixed(1);
    const isChrome = /Chrome|chrome/.test(t.tool);
    const label = trimToolName(t.tool);
    const dot = isChrome ? '<span class="dev-badge dev-pc" style="margin-right:4px">chrome</span>' : '';
    return `
      <div class="tm-row">
        <span class="tm-label" title="${escapeHtml(t.tool)}">${dot}${escapeHtml(label)}</span>
        <div class="tm-bar-wrap"><div class="tm-bar" style="width:${pct}%"></div></div>
        <span class="tm-val">${fmtInt(t.calls)}× · ${fmtInt(t.tot)}t · ${fmtInt(t.avg)}t/c</span>
      </div>
    `;
  }).join("");
}

const ERR_CAT_LABELS_FULL = {
  timeout: "timeout",
  file_not_found: "dosya yok",
  permission_denied: "permission denied",
  command_not_found: "komut yok",
  python_error: "python hatası",
  bash_failure: "bash exit 1",
  stale_reference: "stale reference",
  output_too_large: "output çok büyük",
  rate_limited: "rate limit",
  other_error: "diğer",
};

function renderBehavior(report) {
  const e = report.errors || { total: 0, by_category: [], recent_samples: [] };
  const headEl = document.getElementById("bhv-headline");
  const barsEl = document.getElementById("bhv-bars");
  const recentEl = document.getElementById("bhv-recent");
  if (!headEl) return;

  if (!e.total) {
    headEl.innerHTML = `<strong class="count" style="color:var(--good)">0</strong> hata son 24h <span class="sub">🟢 temiz</span>`;
    barsEl.innerHTML = "";
    recentEl.textContent = "";
    return;
  }

  // Headline
  headEl.innerHTML =
    `<strong class="count">${fmtInt(e.total)}</strong> hata son 24h ` +
    `<span class="sub">· lifetime ${fmtInt(e.total_lifetime || 0)}</span>`;

  // Bars (top 5 categories)
  const cats = (e.by_category || []).slice(0, 5);
  const max = cats[0]?.count || 1;
  barsEl.innerHTML = cats.map((c, i) => {
    const pct = (c.count / max * 100).toFixed(1);
    const klass = i === 0 ? "" : (i === 1 ? " mid" : " warn");
    const label = ERR_CAT_LABELS_FULL[c.category] || c.category;
    return `
      <div class="bhv-row">
        <span class="bhv-label">${escapeHtml(label)}</span>
        <div class="bhv-bar-wrap"><div class="bhv-bar${klass}" style="width:${pct}%"></div></div>
        <span class="bhv-val">${fmtInt(c.count)} (%${(c.share*100).toFixed(0)})</span>
      </div>
    `;
  }).join("");

  // Most recent sample
  const last = (e.recent_samples || [])[0];
  if (last) {
    const label = ERR_CAT_LABELS_FULL[last.category] || last.category;
    const snippet = (last.snippet || "").slice(0, 110);
    recentEl.innerHTML =
      `son hata: <span class="err-cat">[${label}]</span> ${escapeHtml(snippet)}${last.snippet?.length > 110 ? "…" : ""}`;
  } else {
    recentEl.textContent = "";
  }
}

// --- 1. Hero ---------------------------------------------------------------

function renderHero(report) {
  const head = report.fuel_efficiency?.headline || {};
  const el = document.getElementById("hero");
  el.classList.remove("good", "warn", "bad", "neutral");
  el.classList.add(head.kind || "neutral");
  document.getElementById("hero-label").textContent = head.label || "Henüz yeterli veri yok";
  document.getElementById("hero-detail").textContent = head.detail || "";
}

// Codex Pro plan-limit paneli — 5h + haftalık window'lar, per-device.
// Codex'in GERÇEK bağlayıcı kısıtı (abonelik flat-fee, $ notional). Cihan'ın
// görmek istediği asıl sinyal: "haftalık limite ne kadar kaldı".
function renderCodexRateLimit(report, src) {
  const sec = document.getElementById("codex-ratelimit");
  if (!sec) return;
  const rl = report.codex_rate_limits;
  if (src !== "codex" || !rl || typeof rl !== "object" || !Object.keys(rl).length) {
    sec.style.display = "none";
    return;
  }
  sec.style.display = "";
  // plan_type — herhangi bir device'tan al
  const anyDev = Object.values(rl).find(d => d && d.plan_type) || Object.values(rl)[0] || {};
  document.getElementById("rl-plan").textContent =
    (anyDev.plan_type ? anyDev.plan_type.toUpperCase() + " plan" : "abonelik") +
    " · $ değil, plan limiti asıl kısıt";

  const nowSec = Date.now() / 1000;
  const bar = (label, win) => {
    if (!win) return "";
    const pct = Math.max(0, Math.min(100, win.used_percent || 0));
    const cls = pct >= 90 ? "bad" : pct >= 70 ? "warn" : "good";
    let resetStr = "";
    if (win.resets_at) {
      const rem = Math.max(0, win.resets_at - nowSec);
      const h = Math.floor(rem / 3600), m = Math.floor((rem % 3600) / 60);
      resetStr = h >= 1 ? `${h}sa ${m}dk` : `${m}dk`;
    }
    return `
      <div class="rl-bar-row">
        <span class="rl-bar-label">${label}</span>
        <div class="rl-bar-track"><div class="rl-bar-fill ${cls}" style="width:${pct}%"></div></div>
        <span class="rl-bar-pct ${cls}">%${pct.toFixed(0)}</span>
        <span class="rl-bar-reset dim">${resetStr ? "reset " + resetStr : ""}</span>
      </div>`;
  };

  const order = ["mac", "pc"];
  const cards = order.filter(dev => rl[dev]).map(dev => {
    const d = rl[dev];
    return `
      <div class="rl-card">
        <div class="rl-dev"><span class="dev-badge dev-${dev}">cihan-${dev}</span>
          <span class="dim rl-limitname">${escapeHtml(d.limit_name || d.limit_id || "")}</span></div>
        ${bar("5 saat", d.primary)}
        ${bar("haftalık", d.secondary)}
      </div>`;
  }).join("");
  document.getElementById("rl-grid").innerHTML = cards ||
    `<div class="dim">Rate-limit verisi yok.</div>`;
}

// --- 2. Speedometer (SVG) --------------------------------------------------

function polarToCartesian(cx, cy, r, angleDeg) {
  const rad = angleDeg * Math.PI / 180;
  return { x: cx + r * Math.cos(rad), y: cy - r * Math.sin(rad) };
}

function arcPath(cx, cy, r, startVal, endVal, maxVal) {
  const a1 = 180 - (Math.min(startVal, maxVal) / maxVal) * 180;
  const a2 = 180 - (Math.min(endVal,   maxVal) / maxVal) * 180;
  const p1 = polarToCartesian(cx, cy, r, a1);
  const p2 = polarToCartesian(cx, cy, r, a2);
  return `M ${p1.x.toFixed(1)} ${p1.y.toFixed(1)} A ${r} ${r} 0 0 1 ${p2.x.toFixed(1)} ${p2.y.toFixed(1)}`;
}

function renderSpeedometer(report, opts) {
  // opts.device: undefined (Mac, default) | "mac" | "pc". UI elementi:
  // svgId/zoneId/pickerId — Cihan paradigm 2026-05-29: cihan-mac + cihan-pc
  // yan yana iki ayrı speedometer.
  opts = opts || {};
  const device = opts.device || "mac";
  const svgId = opts.svgId || "speedo-svg";
  const zoneId = opts.zoneId || "speedo-zone";
  const pickerId = opts.pickerId || "burn-window-picker";
  const lsKey = opts.lsKey || "ccmeter_burn_hours";

  const fc = report.forecast || {};
  const cw = report.current_window || {};
  const zones = report.burn_rate_zones || { typical: 4, busy: 12, heavy: 25, max: 50 };
  const hoursStr = localStorage.getItem(lsKey) || "2";

  // PC için by_device.pc.burn_rates_by_hours, Mac için forecast.burn_rates_by_hours
  // (Mac'te forecast tüm record'ları kapsar — by_device.mac aynısı; tutarlılık için
  // device==pc'de yalnızca by_device.pc'yi kullan).
  let burnByHours;
  if (device === "pc") {
    burnByHours = ((report.by_device || {}).pc || {}).burn_rates_by_hours || {};
  } else {
    burnByHours = ((report.by_device || {}).mac || {}).burn_rates_by_hours || fc.burn_rates_by_hours || {};
  }
  const currentRate = burnByHours[hoursStr] ?? 0;
  if (device === "mac") window.__currentBurnHours = hoursStr;

  const cx = 200, cy = 200, r = 150, max = zones.max || 50;

  const arcs = `
    <path class="speedo-zone-green"  d="${arcPath(cx, cy, r, 0,             zones.typical, max)}" stroke-width="26" fill="none" />
    <path class="speedo-zone-yellow" d="${arcPath(cx, cy, r, zones.typical, zones.busy,    max)}" stroke-width="26" fill="none" />
    <path class="speedo-zone-orange" d="${arcPath(cx, cy, r, zones.busy,    zones.heavy,   max)}" stroke-width="26" fill="none" />
    <path class="speedo-zone-red"    d="${arcPath(cx, cy, r, zones.heavy,   max,           max)}" stroke-width="26" fill="none" />
  `;

  const ticks = [
    { v: 0,             label: "$0" },
    { v: zones.typical, label: `${fmtMoney(zones.typical)} typical` },
    { v: zones.busy,    label: `${fmtMoney(zones.busy)} busy` },
    { v: zones.heavy,   label: `${fmtMoney(zones.heavy)} heavy` },
  ];
  const ticksSvg = ticks.map(t => {
    const a = 180 - (Math.min(t.v, max) / max) * 180;
    const p = polarToCartesian(cx, cy, r + 22, a);
    const anchor = a < 90 ? "start" : (a > 90 ? "end" : "middle");
    return `<text class="speedo-tick-label" x="${p.x.toFixed(1)}" y="${p.y.toFixed(1)}" text-anchor="${anchor}" dominant-baseline="middle">${t.label}</text>`;
  }).join("");

  const clamped = Math.max(0, Math.min(currentRate, max));
  const needleAngle = 180 - (clamped / max) * 180;
  const tip  = polarToCartesian(cx, cy, r - 5, needleAngle);
  const tail = polarToCartesian(cx, cy, -25,   needleAngle);

  let zoneColor, zoneWord;
  if (currentRate >= zones.heavy)        { zoneColor = "#f85149"; zoneWord = "🔴 kırmızı bölge · çok yoğun"; }
  else if (currentRate >= zones.busy)    { zoneColor = "#f0883e"; zoneWord = "🟠 turuncu bölge · yoğun"; }
  else if (currentRate >= zones.typical) { zoneColor = "#d29922"; zoneWord = "🟡 sarı bölge · normal üstü"; }
  else                                    { zoneColor = "#3fb950"; zoneWord = "🟢 yeşil bölge · sakin"; }

  const svg = document.getElementById(svgId);
  if (!svg) return;
  svg.innerHTML = `
    ${arcs}
    ${ticksSvg}
    <line x1="${tail.x.toFixed(1)}" y1="${tail.y.toFixed(1)}"
          x2="${tip.x.toFixed(1)}"  y2="${tip.y.toFixed(1)}"
          stroke="${zoneColor}" stroke-width="4" stroke-linecap="round" />
    <circle class="speedo-hub" cx="${cx}" cy="${cy}" r="9" />
    <text class="speedo-value" x="${cx}" y="${cy + 38}">${fmtMoney(currentRate)}</text>
    <text class="speedo-unit"  x="${cx}" y="${cy + 54}">saatte · son ${hoursStr === "0.25" ? "15dk" : hoursStr + "h"} ortalama</text>
  `;

  // Per-model breakdown of this burn rate — answers "Opus mu Sonnet mi yiyor?"
  // PC için forecast.burn_rates_by_hours_by_model Mac-only veriyor (Mac
  // forecast'ı tüm record'ları sayıyor ama device filter yapmıyor). PC için
  // doğru veriyi vermiyoruz — şimdilik PC speedo'da breakdown gizli.
  const breakdown = device === "pc"
    ? []
    : ((fc.burn_rates_by_hours_by_model || {})[hoursStr] || []);
  let breakdownHtml = "";
  if (breakdown.length) {
    const visibleModels = breakdown
      .filter(m => !String(m.model_id).startsWith("<"))
      .slice(0, 4);
    breakdownHtml = `<div class="speedo-breakdown">` +
      visibleModels.map(m => {
        const fam = modelToFamily(m.model_id);
        return `<span class="sb-row sb-${fam}">` +
          `<span class="sb-name">${escapeHtml(modelDisplay(m.model_id))}</span> ` +
          `<span class="sb-val">${fmtMoney(m.cost_per_hour)}/sa</span> ` +
          `<span class="sb-share">(%${(m.share * 100).toFixed(0)})</span>` +
        `</span>`;
      }).join("") +
    `</div>`;
  }

  const zoneEl = document.getElementById(zoneId);
  if (zoneEl) {
    zoneEl.innerHTML =
      `${zoneWord} · tipiğin <strong>${fmtMoney(zones.typical)}/sa</strong>, yoğunun <strong>${fmtMoney(zones.busy)}/sa</strong>` +
      breakdownHtml;
  }
}

// --- 2b. Trip computer (today's narrative) --------------------------------

function renderTrip(report) {
  const fc = report.forecast || {};
  const cw = report.current_window || {};
  const daily = report.daily || [];
  const sessions = report.by_session || [];
  const today = new Date().toISOString().slice(0, 10);
  const todayEntry = daily.find(d => d.date === today);

  // Big number: today's $ from daily totals (UTC).
  document.getElementById("trip-amount").textContent = fmtMoney(fc.today?.so_far ?? 0);

  // Turn count: from daily.messages — honest counter that doesn't suffer
  // from session-crosses-midnight ambiguity.
  const turns = todayEntry?.messages ?? 0;
  const tokens = todayEntry?.total_tokens ?? 0;
  document.getElementById("trip-line").innerHTML =
    turns
      ? `<strong>${fmtInt(turns)}</strong> assistant turn · <strong>${fmtInt(tokens)}</strong> token (UTC günü)`
      : "Bugün UTC'de henüz aktivite yok.";

  // Most recent activity: latest session by ended_at (the LAST turn time,
  // not the session's start — sessions can run for days).
  if (sessions.length) {
    const latest = sessions[0];
    document.getElementById("trip-top-project").innerHTML =
      `En son aktivite: <strong>${relTime(latest.ended_at || latest.started_at)}</strong> · ${escapeHtml(latest.project_label)} (${fmtMoney(latest.cost_usd)})`;
  } else {
    document.getElementById("trip-top-project").textContent = "";
  }

  // Today's projection (end-of-day estimate).
  if (fc.today?.projected_eod != null && turns) {
    document.getElementById("trip-top-session").innerHTML =
      `Gün sonu tahmini: <strong>${fmtMoney(fc.today.projected_eod)}</strong> (kalan saatler tipik tempoda)`;
  } else {
    document.getElementById("trip-top-session").textContent = "";
  }

  // 5-hour window state.
  if (cw.active) {
    const mins = Math.floor((cw.remaining_seconds || 0) / 60);
    const hr = Math.floor(mins / 60);
    const mm = mins % 60;
    const timeLeft = hr > 0 ? `${hr}sa ${mm}dk` : `${mm}dk`;
    document.getElementById("trip-window").innerHTML =
      `5-saat pencere: <strong>${timeLeft}</strong> kaldı · şu an tempo <strong>${fmtMoney(cw.cost_per_hour || 0)}/saat</strong>`;
  } else {
    document.getElementById("trip-window").textContent =
      "Aktif 5-saat penceren yok — son zamanda Claude Code kullanmamışsın.";
  }

  // --- Block ETA — "ne zaman senin P90 blok limitin patlar?"
  renderBlockEta(cw);

  // --- Weekly cap — rolling 7-gün vs senin P95 haftan
  renderWeeklyCap(report.weekly_cap || {});

  // --- Device split — Cihan paradigm 2026-05-29: Mac + PC ayrı sayaç.
  renderDeviceSplit(report.by_device || {});
}

function renderDeviceSplit(byDev) {
  let host = document.getElementById("device-split");
  if (!host) {
    const trip = document.querySelector(".status-trip");
    if (!trip) return;
    host = document.createElement("div");
    host.id = "device-split";
    host.style.marginTop = "6px";
    host.style.paddingTop = "6px";
    host.style.borderTop = "1px solid var(--bg-3)";
    trip.appendChild(host);
  }
  const mac = byDev.mac || {};
  const pc = byDev.pc || {};
  const macToday = (mac.today || {}).cost_usd || 0;
  const pcToday  = (pc.today  || {}).cost_usd || 0;
  const macH1    = mac.burn_rate_usd_per_hour || 0;
  const pcH1     = pc.burn_rate_usd_per_hour  || 0;
  host.innerHTML = `
    <div class="device-row"><span class="label"><span class="dev-badge dev-mac">mac</span> bugün</span><span class="val">${fmtMoney(macToday)} · ${fmtMoney(macH1)}/sa</span></div>
    <div class="device-row"><span class="label"><span class="dev-badge dev-pc">pc</span> bugün</span><span class="val">${fmtMoney(pcToday)} · ${fmtMoney(pcH1)}/sa</span></div>
  `;
}

function renderBlockEta(cw) {
  const el = document.getElementById("trip-block");
  if (!cw.active) {
    el.className = "trip-line dim";
    el.textContent = "";
    return;
  }
  const used = cw.cost_usd || 0;
  const p50 = cw.baseline_cost_p50 || 0;
  const p90 = cw.baseline_cost_p90 || 0;
  const remMin = Math.floor((cw.remaining_seconds || 0) / 60);

  let kind, msg, tip;
  if (cw.block_already_past_p90) {
    kind = "bad";
    msg = `🔴 Blok <strong>${fmtMoney(used)}</strong> · P90 (${fmtMoney(p90)}) aşıldı`;
    tip = `Bu blokta ${fmtMoney(used)} harcadın — P90 yoğun bloğunu (${fmtMoney(p90)}) çoktan aştın. ${remMin}dk daha var.`;
  } else if (cw.block_will_hit_p90) {
    const m = cw.block_minutes_to_p90;
    const eta = cw.block_eta_p90_iso ? new Date(cw.block_eta_p90_iso).toLocaleTimeString("tr-TR", {hour:"2-digit",minute:"2-digit"}) : "";
    kind = "bad";
    msg = `🔴 ${m}dk sonra P90 (${fmtMoney(p90)})`;
    tip = `Bu hızla ${m}dk sonra (saat ${eta}) P90 blok limitine (${fmtMoney(p90)}) varıyorsun.`;
  } else if (cw.block_already_past_p50) {
    kind = "warn";
    msg = `🟠 Blok <strong>${fmtMoney(used)}</strong> · P50 (${fmtMoney(p50)}) aşıldı`;
    tip = `P50 medyan blokunu (${fmtMoney(p50)}) aştın, P90 (${fmtMoney(p90)})'a kadar boş alan var.`;
  } else if (cw.block_will_hit_p50) {
    const m = cw.block_minutes_to_p50;
    const eta = cw.block_eta_p50_iso ? new Date(cw.block_eta_p50_iso).toLocaleTimeString("tr-TR", {hour:"2-digit",minute:"2-digit"}) : "";
    kind = "warn";
    msg = `🟡 ${m}dk sonra P50 (${fmtMoney(p50)})`;
    tip = `${m}dk sonra (saat ${eta}) medyan blok limitine (${fmtMoney(p50)}) varacaksın.`;
  } else {
    kind = "good";
    msg = `🟢 Blok <strong>${fmtMoney(used)}</strong> · sakin`;
    tip = `Sakin tempodasın (medyan ${fmtMoney(p50)}, yoğun ${fmtMoney(p90)}).`;
  }
  el.className = "trip-line trip-block " + kind;
  el.innerHTML = msg;
  el.title = tip;
}

function renderWeeklyCap(wc) {
  const el = document.getElementById("trip-week");
  if (!wc.samples) {
    el.className = "trip-line dim";
    el.textContent = "Haftalık karşılaştırma için yeterli geçmiş yok.";
    return;
  }
  const recent = wc.rolling_7d_cost;
  const p95 = wc.historical_7d_p95;
  const pct = wc.pct_of_personal_p95;
  const kind = wc.verdict_kind || "good";
  const label = wc.verdict_label || "";
  const deltaSign = wc.delta_pct >= 0 ? "↑" : "↓";

  const dot = kind === "bad" ? "🔴" : kind === "warn" ? "🟡" : "🟢";
  el.className = "trip-line trip-week " + kind;
  el.innerHTML =
    `${dot} Hafta <strong>${fmtMoney(recent)}</strong> · P95 ${fmtMoney(p95)} · <strong>%${pct.toFixed(0)}</strong> ` +
    `<span class="dim">${deltaSign}${Math.abs(wc.delta_pct).toFixed(0)}%</span>`;
  el.title = `Bu hafta ${fmtMoney(recent)} · senin P95 haftan ${fmtMoney(p95)} · %${pct.toFixed(0)} (${label}) · ${deltaSign}${Math.abs(wc.delta_pct).toFixed(0)}% vs önceki hafta`;
}

// --- 3. Recent activity (banka ekstresi) ----------------------------------

function renderRecent(report) {
  // 2026-05-29 (Cihan paradigm v4): "anlık her turne göre, toplayarak gitmesin".
  // Her satır = bir assistant TURN (mesaj). Oturum başına aggregate YOK.
  // Aynı oturumun 5 turn'ü = 5 ayrı satır. Backend `recent_turns` field'ını
  // veriyor — son 30 record, chronological desc, her birinin effective tokens.
  const turns = (report.recent_turns || []).filter(t => !(String(t.model || "")).startsWith("<"));
  const tbody = document.querySelector("#recent-table tbody");
  if (!turns.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="dim">Henüz aktivite yok.</td></tr>`;
    return;
  }
  tbody.innerHTML = turns.map(t => {
    const model = t.model || "";
    const fam = modelToFamily(model);
    const dev = t.device || "mac";
    const devBadge = `<span class="dev-badge dev-${dev}">${dev}</span>`;
    const pills = model ? `<span class="pill ${fam}">${modelDisplay(model)}</span>` : "";
    const eff = t.effective_tokens;
    const cost = t.cost_usd;
    const cacheRate = t.cache_hit_rate;
    const sidShort = (t.session_id || "").slice(0, 8);
    return `
      <tr>
        <td>${relTime(t.timestamp)}</td>
        <td class="cell-project">
          <div>${escapeHtml(t.project_label)} ${devBadge}</div>
          <div class="intent-text dim" title="session ${escapeHtml(t.session_id || "")}">sid ${escapeHtml(sidShort)}</div>
        </td>
        <td>${pills}</td>
        <td class="num">${fmtInt(eff)}</td>
        <td class="num">${fmtMoney(cost)}</td>
        <td class="num">${fmtPct(cacheRate)}</td>
      </tr>
    `;
  }).join("");
}

// --- 2c. Live panel — multi-model token burn (last 15 min) -----------------

function renderLive(report) {
  const winStr = localStorage.getItem("ccmeter_live_window") || "1440";
  const byWindow = report.live_active_models_by_window || {};
  const live = byWindow[winStr]
            || report.live_active_models
            || { models: [], lookback_minutes: parseInt(winStr) };
  const cw = report.current_window || {};
  const listEl = document.getElementById("live-models-list");

  // Filter out synthetic — that's an internal aggregator, not a real model.
  const models = (live.models || []).filter(m => !String(m.model_id).startsWith("<"));

  // Human-friendly label for the lookback window.
  const winLabel = winStr === "15"   ? "son 15dk"
                 : winStr === "60"   ? "son 1 saat"
                 : winStr === "300"  ? "son 5 saat"
                 : winStr === "1440" ? "son 24 saat"
                                     : `son ${winStr}dk`;

  if (!models.length) {
    listEl.innerHTML = `<div class="dim" style="padding:8px">${winLabel}'te aktif model yok.</div>`;
  } else {
    listEl.innerHTML = models.map(m => {
      const secs = m.seconds_since_last ?? 999;
      // Active = <60sec, Cooling = <300sec, Idle = older
      const klass = secs < 60 ? "active" : secs < 300 ? "cooling" : "idle";
      const lastWord = secs < 60   ? `${secs}sn önce`
                     : secs < 3600 ? `${Math.floor(secs/60)}dk önce`
                                   : `${Math.floor(secs/3600)}sa önce`;
      return `
        <div class="live-model-row ${klass}">
          <div class="live-model-name">${modelDisplay(m.model_id)}</div>
          <div class="live-model-rate">
            <span class="rate-tok">${fmtInt(Math.round(m.tokens_per_min))}/dk</span>
            <span class="rate-cost">${fmtMoney(m.cost_per_hour ?? 0)}/sa</span>
          </div>
          <div class="live-model-sub">${fmtInt(m.billable_tokens_recent)} billable tok · ${m.messages} turn · son turn ${lastWord}</div>
        </div>
      `;
    }).join("");
  }

  // Window summary line at the bottom of the panel.
  if (cw.active) {
    const remMin = Math.floor((cw.remaining_seconds || 0) / 60);
    document.getElementById("live-window-summary").innerHTML =
      `aktif modeller: ${winLabel} · 5h pencere: ${fmtInt(cw.billable_tokens_used || 0)} billable tok · ${remMin}dk kaldı`;
  } else {
    document.getElementById("live-window-summary").textContent = `aktif modeller: ${winLabel} · 5h pencere yok.`;
  }
}

const ERROR_LABELS = {
  timeout: "timeout",
  file_not_found: "dosya yok",
  permission_denied: "permission",
  command_not_found: "komut yok",
  python_error: "python hatası",
  bash_failure: "bash fail",
  stale_reference: "stale ref",
  output_too_large: "output büyük",
  rate_limited: "rate limit",
  other_error: "diğer",
};

function renderErrorSummary(err) {
  const el = document.getElementById("error-summary");
  if (!el) return;
  const total = err.total ?? 0;
  if (!total) {
    el.innerHTML = `<span class="dim">son 24h hata: <strong style="color:var(--good)">0</strong> 🟢</span>`;
    return;
  }
  const top = (err.by_category || []).slice(0, 3);
  const pieces = top.map(c => {
    const label = ERROR_LABELS[c.category] || c.category;
    return `<span class="cat">${label} ${(c.share*100).toFixed(0)}%</span>`;
  }).join(" · ");
  el.innerHTML = `son 24h hata: <strong>${total}</strong> · ${pieces}`;
}

// --- 2d. Models breakdown (specific versions, not just families) -----------

function renderModels(report) {
  // Prefer by_model_full (specific versions like claude-opus-4-7) over the
  // family-collapsed by_model. Falls back if backend hasn't been restarted.
  const rows = report.by_model_full || report.by_model || [];
  const tbody = document.querySelector("#models-table tbody");
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="3" class="dim">Veri yok</td></tr>`;
    return;
  }
  const sorted = [...rows].sort((a, b) => (b.cost_usd || 0) - (a.cost_usd || 0));
  tbody.innerHTML = sorted.map(r => {
    const id = r.model_id || r.model_family || "";
    const family = modelToFamily(id);
    const display = modelDisplay(id);
    return `
      <tr>
        <td><span class="pill ${family}">${display}</span></td>
        <td class="num">${fmtInt(r.messages)}</td>
        <td class="num">${fmtMoney(r.cost_usd)}</td>
      </tr>
    `;
  }).join("");
}

// --- 3b. Top projects (lifetime) -------------------------------------------

function renderProjects(report) {
  const projects = (report.by_project || []).slice(0, 8);
  const tbody = document.querySelector("#projects-table tbody");
  if (!projects.length) {
    tbody.innerHTML = `<tr><td colspan="3" class="dim">Proje yok</td></tr>`;
    return;
  }
  // 2026-05-28 (Cihan paradigm): "EN PAHALI PROJELER · LİFETİME"
  // panelinde token toplamı da göster — Son aktivite paneli artık
  // gerçek-zamanlı delta gösterdiği için lifetime totals'a buranın
  // ihtiyacı doğdu. Kolon order: PROJE | TOKEN | $.
  tbody.innerHTML = projects.map(p => `
    <tr>
      <td>${escapeHtml(p.project_label)}</td>
      <td class="num">${fmtInt(p.total_tokens || 0)}</td>
      <td class="num">${fmtMoney(p.cost_usd)}</td>
    </tr>
  `).join("");
}

// --- 4. Daily chart (one simple bar chart) ---------------------------------

let dailyChart = null;

function renderDailyChart(report) {
  const daily = (report.daily || []).slice(-30);
  const labels = daily.map(d => d.date.slice(5));   // MM-DD
  const costs  = daily.map(d => d.cost_usd);

  // Personal median = baseline for coloring bars
  const sortedCosts = [...costs].filter(c => c > 0).sort((a, b) => a - b);
  const median = sortedCosts.length ? sortedCosts[Math.floor(sortedCosts.length / 2)] : 0;
  const colors = costs.map(c => {
    if (median <= 0) return "#58a6ff";
    if (c >= median * 2) return "#f85149";    // way above
    if (c >= median * 1.2) return "#d29922";  // somewhat above
    return "#3fb950";                          // around or below
  });

  if (dailyChart) dailyChart.destroy();
  dailyChart = new Chart(document.getElementById("chart-daily"), {
    type: "bar",
    data: {
      labels,
      datasets: [{ label: "$ harcama", data: costs, backgroundColor: colors }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      animation: false,    // no grow-from-zero on every refresh
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: "#6b7785", maxRotation: 0, autoSkip: true, maxTicksLimit: 15 }, grid: { color: "#1a1f26" } },
        y: { beginAtZero: true, ticks: { color: "#6b7785", callback: v => "$" + v.toLocaleString() }, grid: { color: "#1a1f26" } },
      },
    },
  });
}

// --- 4b. Long-term burn rate trend chart (bars + rolling 7d avg) -----------

let trendChart = null;

// Trading-chart style: pick timeframe (bucket size), overlay one or more
// moving averages computed over BAR count.
const MA_COLORS = { 7: "#f85149", 25: "#f0883e", 50: "#d29922", 100: "#b794f6" };

function movingAverage(arr, n) {
  return arr.map((_, i) => {
    const win = arr.slice(Math.max(0, i - n + 1), i + 1);
    return win.reduce((a, b) => a + b, 0) / win.length;
  });
}

function formatBucketLabel(iso, tfHours) {
  const d = new Date(iso);
  if (tfHours >= 24) {
    return d.toISOString().slice(5, 10);           // MM-DD
  }
  // Show date if it crosses midnight, otherwise HH:00
  const hh = d.getUTCHours().toString().padStart(2, "0");
  return tfHours >= 6
    ? `${d.toISOString().slice(5, 10)} ${hh}:00`
    : `${hh}:00`;
}

function renderTrendChart(report) {
  const tfStr = localStorage.getItem("ccmeter_trend_tf") || "24";
  const tfH = parseInt(tfStr, 10);
  const maListRaw = localStorage.getItem("ccmeter_trend_ma");
  const maList = maListRaw ? JSON.parse(maListRaw) : [7];

  const series = (report.burn_rate_timeseries?.[tfStr]) || [];
  const labels = series.map(b => formatBucketLabel(b.start, tfH));
  const burns  = series.map(b => b.cost_per_hour);

  // Build datasets: one bar series + one line per selected MA.
  const datasets = [
    {
      type: "bar",
      label: `$/saat (${tfH < 24 ? tfH + "h" : "1d"})`,
      data: burns,
      backgroundColor: "rgba(88, 166, 255, 0.35)",
      borderColor: "transparent",
      order: 10,
    },
  ];

  for (const n of maList) {
    const ma = movingAverage(burns, n);
    datasets.push({
      type: "line",
      label: `MA ${n}`,
      data: ma,
      borderColor: MA_COLORS[n] || "#ffffff",
      borderWidth: 2,
      fill: false,
      tension: 0.25,
      pointRadius: 0,
      pointHoverRadius: 3,
      order: 1,
    });
  }

  // For lit-compare we use the first active MA's most-recent value.
  const youCurrent = (() => {
    if (!burns.length) return 0;
    if (!maList.length) return burns[burns.length - 1];
    const firstMA = movingAverage(burns, maList[0]);
    return firstMA[firstMA.length - 1] || 0;
  })();

  if (trendChart) trendChart.destroy();
  trendChart = new Chart(document.getElementById("chart-trend"), {
    type: "bar",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: {
          labels: { color: "#9aa7b3", font: { size: 10 }, boxWidth: 10, padding: 8 },
          position: "top",
          align: "end",
        },
        tooltip: {
          callbacks: {
            label: ctx => `${ctx.dataset.label}: $${(ctx.parsed.y || 0).toFixed(2)}/saat`,
          },
        },
      },
      scales: {
        x: {
          ticks: { color: "#6b7785", maxRotation: 0, autoSkip: true, maxTicksLimit: 12 },
          grid: { color: "#1a1f26" },
        },
        y: {
          beginAtZero: true,
          ticks: { color: "#6b7785", callback: v => "$" + v + "/sa" },
          grid: { color: "#1a1f26" },
        },
      },
    },
  });

  // Lit-compare interprets reference values based on the selected
  // timeframe so the comparison is apples-to-apples:
  //   - 1d (24h bar): show daily totals (industry $13/day, $30/day).
  //   - sub-day TF:   show hourly active rates ($5/sa, $15/sa).
  renderLitCompare(youCurrent, tfH, maList[0] || null);
}

function renderLitCompare(youHourly, tfH, maPeriod) {
  const el = document.getElementById("lit-compare");
  if (!el) return;

  // Decide the comparison unit based on timeframe:
  //   * 1d (24h) → daily totals (Anthropic published $13/day, $30/day P90)
  //   * sub-day  → hourly active rates ($5/sa, $15/sa derived from above)
  const dailyMode = tfH >= 24;
  let avg, p90, unit, youVal, unitLabel;
  if (dailyMode) {
    // For 1d, the bar value already represents "this day's $/sa calendar
    // average". Multiply by 24 to get $/day (which matches the
    // Anthropic-published daily totals).
    avg = 13;
    p90 = 30;
    unit = "/gün";
    unitLabel = "günlük toplam";
    youVal = youHourly * 24;
  } else {
    avg = 5;
    p90 = 15;
    unit = "/sa";
    unitLabel = "saatlik aktif";
    youVal = youHourly;
  }

  const youName = maPeriod ? `Senin MA(${maPeriod})` : "Senin son bar";

  const benchmarks = [
    { name: "Endüstri avg", val: avg, cls: "lit-avg" },
    { name: "Endüstri P90", val: p90, cls: "lit-p90" },
    { name: youName,        val: youVal, cls: "lit-you" },
  ];
  const maxVal = Math.max(...benchmarks.map(b => b.val), p90 * 1.3);

  // Verdict relative to industry P90 for the chosen unit.
  const ratioP90 = youVal / p90;
  const ratioAvg = youVal / avg;
  let verdictKlass, verdictWord;
  if (ratioP90 >= 3)       { verdictKlass = "bad";  verdictWord = "çok yüksek"; }
  else if (ratioP90 >= 1)  { verdictKlass = "bad";  verdictWord = "yüksek"; }
  else if (ratioP90 >= 0.5){ verdictKlass = "warn"; verdictWord = "normal-üstü"; }
  else                      { verdictKlass = "good"; verdictWord = "normal/altı"; }

  const rowsHtml = benchmarks.map(b => `
    <div class="lit-row ${b.cls}">
      <span class="lit-label">${b.name}</span>
      <div class="lit-bar-wrap">
        <div class="lit-bar" style="width:${(b.val / maxVal * 100).toFixed(1)}%"></div>
      </div>
      <span class="lit-val">$${b.val.toFixed(2)}${unit}</span>
    </div>
  `).join("");

  const unitNote = `<div class="lit-unit-hint">birim: <strong>${unit}</strong> · ${unitLabel} (timeframe '${tfH >= 24 ? "1d" : tfH + "h"}'a göre)</div>`;

  const ratioHtml = youVal > 0
    ? `<div class="lit-ratio ${verdictKlass}">
         Şu anki tempon endüstri P90'ın <strong>${ratioP90.toFixed(1)}×</strong>'i,
         ortalama dev'in <strong>${ratioAvg.toFixed(1)}×</strong>'i — <strong>${verdictWord}</strong>.
       </div>`
    : "";

  el.innerHTML = unitNote + rowsHtml + ratioHtml;
}

// --- 6. Footer (small lifetime summary) ------------------------------------

function renderFooter(report) {
  const t = report.totals || {};
  const ga = report.git_attribution || {};
  const parts = [
    `lifetime: <strong>${fmtMoney(t.cost_usd || 0)}</strong>`,
    `<strong>${fmtInt(t.messages || 0)}</strong> turn`,
    `<strong>${fmtPct(t.cache_hit_rate || 0)}</strong> cache hit`,
  ];
  if (ga.total_commits_matched) {
    parts.push(`<strong>${ga.total_commits_matched}</strong> commit (son 14g, median ${fmtMoney(ga.median_cost_usd)}/commit)`);
  }
  document.getElementById("footer-text").innerHTML = parts.join(" · ");
}

// --- Refresh loop ----------------------------------------------------------

async function refresh(force = false) {
  // In-flight guard — Codex raporu ilk yüklemede yavaş olabilir (büyük veri).
  // Üst üste binen auto-refresh'ler isteği abort ettirip "Failed to fetch"
  // veriyordu. Aynı anda tek istek: bir tanesi uçarken yenisini atlıyoruz.
  if (window.__refreshInFlight && !force) return;
  window.__refreshInFlight = true;
  try {
    const report = await loadReport(force);
    // Stale-source guard: kullanıcı fetch sırasında kaynağı değiştirdiyse
    // (ccmeter↔codex_meter), gecikmeli gelen eski kaynağın raporunu UYGULAMA.
    if ((report._meta?.source || "claude") !== window.__source) return;
    window.__lastReport = report;            // cached for picker-triggered re-renders
    render(report);
    window.__lastRefreshMs = Date.now();
  } catch (err) {
    console.error(err);
    document.getElementById("last-updated").textContent = "load failed: " + err.message;
  } finally {
    window.__refreshInFlight = false;
  }
}

function updateLiveStamp() {
  const el = document.getElementById("last-updated");
  if (!el) return;
  const last = window.__lastRefreshMs;
  if (!last) return;
  const secs = Math.floor((Date.now() - last) / 1000);
  if (secs < 2)       el.textContent = "just refreshed";
  else if (secs < 60) el.textContent = `${secs}s ago · next in ${Math.max(1, 5 - secs)}s`;
  else                el.textContent = `${Math.floor(secs / 60)}m ${secs % 60}s ago`;
}

// --- Resizable cards: persist size + trigger chart resize ---

const CARD_SIZES_KEY = "ccmeter_card_sizes";

function loadCardSizes() {
  try { return JSON.parse(localStorage.getItem(CARD_SIZES_KEY) || "{}"); }
  catch { return {}; }
}

function saveCardSize(id, w, h) {
  const sizes = loadCardSizes();
  sizes[id] = [Math.round(w), Math.round(h)];
  localStorage.setItem(CARD_SIZES_KEY, JSON.stringify(sizes));
}

function applyCardSizes() {
  const sizes = loadCardSizes();
  document.querySelectorAll("[data-card-id]").forEach(card => {
    const id = card.dataset.cardId;
    if (sizes[id]) {
      const [w, h] = sizes[id];
      // Sanity-cap: don't restore absurd sizes (old bug residue).
      const safeW = Math.min(Math.max(w, 240), window.innerWidth - 40);
      const safeH = Math.min(Math.max(h, 140), window.innerHeight - 80);
      card.style.width = safeW + "px";
      card.style.height = safeH + "px";
      card.style.flex = "0 0 auto";   // override flex so explicit size sticks
    }
  });
}

// Only save sizes when the user actively dragged the BOTTOM-RIGHT corner
// (not just clicked a button inside the card). Detect drag start by
// checking pointer position relative to the card's resize-handle region.
function setupResizeObservers() {
  const HANDLE_ZONE = 22;   // px from bottom-right corner that counts as "the handle"
  let dragging = null;      // { card, id } while user is dragging the corner

  document.querySelectorAll("[data-card-id]").forEach(card => {
    card.addEventListener("pointerdown", e => {
      const rect = card.getBoundingClientRect();
      const fromRight = rect.right - e.clientX;
      const fromBottom = rect.bottom - e.clientY;
      if (fromRight >= 0 && fromRight <= HANDLE_ZONE &&
          fromBottom >= 0 && fromBottom <= HANDLE_ZONE) {
        dragging = { card, id: card.dataset.cardId };
      }
    });
  });

  window.addEventListener("pointerup", () => {
    if (!dragging) return;
    const { card, id } = dragging;
    // Save the final on-screen box (offsetWidth/Height includes border + padding).
    saveCardSize(id, card.offsetWidth, card.offsetHeight);
    dragging = null;
  });

  // Cleanup if pointer leaves window mid-drag.
  window.addEventListener("pointercancel", () => { dragging = null; });
}

// Reset button: clear saved sizes and reload.
function resetCardSizes() {
  localStorage.removeItem(CARD_SIZES_KEY);
  location.reload();
}
window.resetCardSizes = resetCardSizes;   // expose for manual use

function start() {
  document.getElementById("refresh").addEventListener("click", () => refresh(true));

  // Restore any persisted card sizes BEFORE charts initialize so the
  // first render sizes them correctly.
  applyCardSizes();
  setupResizeObservers();

  // Burn-rate window picker — persists choice in localStorage.
  const saved = localStorage.getItem("ccmeter_burn_hours") || "2";
  document.querySelectorAll("#burn-window-picker button").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.h === saved);
    btn.addEventListener("click", () => {
      const h = btn.dataset.h;
      localStorage.setItem("ccmeter_burn_hours", h);
      document.querySelectorAll("#burn-window-picker button").forEach(b => {
        b.classList.toggle("active", b.dataset.h === h);
      });
      if (window.__lastReport) renderSpeedometer(window.__lastReport, { device: "mac" });
    });
  });

  // PC speedometer window picker (ayrı localStorage key).
  const savedPc = localStorage.getItem("ccmeter_burn_hours_pc") || "2";
  document.querySelectorAll("#burn-window-picker-pc button").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.h === savedPc);
    btn.addEventListener("click", () => {
      const h = btn.dataset.h;
      localStorage.setItem("ccmeter_burn_hours_pc", h);
      document.querySelectorAll("#burn-window-picker-pc button").forEach(b => {
        b.classList.toggle("active", b.dataset.h === h);
      });
      if (window.__lastReport) renderSpeedometer(window.__lastReport, {
        device: "pc", svgId: "speedo-svg-pc", zoneId: "speedo-zone-pc",
        pickerId: "burn-window-picker-pc", lsKey: "ccmeter_burn_hours_pc",
      });
    });
  });

  // Source toggle (ccmeter = Claude / codex_meter = Codex). Switching
  // changes window.__source, persists it, and force-refetches all panels.
  document.querySelectorAll("#source-toggle button").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.source === window.__source);
    btn.addEventListener("click", () => {
      const src = btn.dataset.source;
      if (src === window.__source) return;
      window.__source = src;
      localStorage.setItem("ccmeter_source", src);
      document.querySelectorAll("#source-toggle button").forEach(b => {
        b.classList.toggle("active", b.dataset.source === src);
      });
      // Codex ilk yükleme yavaş olabilir (cache build) — kullanıcıya işaret.
      const du = document.getElementById("last-updated");
      if (du) du.textContent = (src === "codex" ? "codex_meter" : "ccmeter") + " yükleniyor…";
      // Kullanıcı eylemi — in-flight guard'ı sıfırla ki kaynak değişimi anında
      // fetch tetiklesin (eski in-flight fetch stale-source guard'la elenecek).
      window.__refreshInFlight = false;
      refresh(false);
    });
  });

  // Live-panel window picker (15dk / 1sa / 5sa / 24sa).
  const savedLive = localStorage.getItem("ccmeter_live_window") || "1440";
  document.querySelectorAll("#live-window-picker button").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.w === savedLive);
    btn.addEventListener("click", () => {
      const w = btn.dataset.w;
      localStorage.setItem("ccmeter_live_window", w);
      document.querySelectorAll("#live-window-picker button").forEach(b => {
        b.classList.toggle("active", b.dataset.w === w);
      });
      if (window.__lastReport) renderLive(window.__lastReport);
    });
  });

  // Trend chart timeframe picker (1h / 4h / 6h / 12h / 1d).
  const savedTf = localStorage.getItem("ccmeter_trend_tf") || "24";
  document.querySelectorAll("#trend-tf-picker button").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.tf === savedTf);
    btn.addEventListener("click", () => {
      const tf = btn.dataset.tf;
      localStorage.setItem("ccmeter_trend_tf", tf);
      document.querySelectorAll("#trend-tf-picker button").forEach(b => {
        b.classList.toggle("active", b.dataset.tf === tf);
      });
      if (window.__lastReport) renderTrendChart(window.__lastReport);
    });
  });

  // Trend chart MA indicator multi-select (7 / 25 / 50 / 100).
  const savedMARaw = localStorage.getItem("ccmeter_trend_ma");
  const savedMA = new Set(savedMARaw ? JSON.parse(savedMARaw) : [7]);
  document.querySelectorAll(".indicator-btn").forEach(btn => {
    const n = parseInt(btn.dataset.ma, 10);
    btn.classList.toggle("active", savedMA.has(n));
    btn.addEventListener("click", () => {
      if (savedMA.has(n)) savedMA.delete(n);
      else savedMA.add(n);
      // Persist as sorted array.
      const arr = [...savedMA].sort((a, b) => a - b);
      localStorage.setItem("ccmeter_trend_ma", JSON.stringify(arr));
      btn.classList.toggle("active", savedMA.has(n));
      if (window.__lastReport) renderTrendChart(window.__lastReport);
    });
  });

  refresh(false);
  setInterval(() => refresh(false), 5_000);   // faster live updates
  setInterval(updateLiveStamp, 1000);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", start);
} else {
  start();
}
