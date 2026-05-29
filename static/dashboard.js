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

  renderHero(rep, isCodex);
  renderHeroAside(rep, isCodex);
  renderKPIs(rep, isCodex);
  renderLimits(rep, isCodex);
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

// ---------- hero (binding constraint) ----------
function renderHero(rep, isCodex) {
  let pct, label, headline, detail, stats = [];
  if (isCodex) {
    // Binding constraint = weekly rate-limit % (max across devices)
    const rl = rep.codex_rate_limits || {};
    let maxWk = 0, resets = 0, plan = "";
    for (const d of Object.values(rl)) {
      const w = (d.secondary || {}).used_percent || 0;
      if (w >= maxWk) { maxWk = w; resets = (d.secondary || {}).resets_at; }
      plan = d.plan_type || plan;
    }
    pct = maxWk;
    label = "haftalık limit";
    headline = pct >= 90 ? "⚠️ Haftalık Codex limitine çok yakınsın"
      : pct >= 70 ? "Haftalık limit yükseliyor" : "Haftalık limitte bol alan var";
    detail = `Codex ${plan ? plan.toUpperCase() + " plan" : "aboneliği"} · flat-fee, yani asıl kısıt $ değil plan limiti. `
      + (resets ? `Haftalık pencere ${resetIn(resets)} sonra resetlenir.` : "");
  } else {
    // Claude: 5h block fill vs personal P90 (proxy for the wall)
    const cw = rep.current_window || {};
    const used = cw.cost_usd || 0, p90 = cw.baseline_cost_p90 || 0;
    pct = p90 > 0 ? Math.min(100, used / p90 * 100) : 0;
    label = "5 saat bloğu";
    const fe = rep.fuel_efficiency?.headline || {};
    headline = fe.label || "Kullanım profili";
    detail = fe.detail || "";
  }
  const c = sevClass(pct);
  const ring = $("hero-ring");
  ring.style.setProperty("--p", pct.toFixed(0));
  ring.style.setProperty("--c", `var(--${c})`);
  $("hero-ring-value").textContent = "%" + Math.round(pct);
  $("hero-ring-label").textContent = label;
  $("hero-headline").textContent = headline;
  $("hero-detail").textContent = detail;

  // hero stats: today $, burn rate, tokens today
  const fc = rep.forecast || {};
  const t = rep.totals || {};
  const today = fc.today?.so_far ?? 0;
  const burn = fc.burn_rate_per_hour_recent ?? (rep.current_window || {}).cost_per_hour ?? 0;
  stats = [
    { v: fmtMoney0(today), l: "bugün" },
    { v: fmtMoney(burn) + "<small>/sa</small>", l: "burn rate" },
    { v: fmtPct(t.cache_hit_rate), l: "cache hit" },
  ];
  $("hero-stats").innerHTML = stats.map(s => `<div class="hero-stat"><div class="v">${s.v}</div><div class="l">${s.l}</div></div>`).join("");
}

// ---------- hero aside (şu an) ----------
function renderHeroAside(rep, isCodex) {
  const fc = rep.forecast || {};
  const cw = rep.current_window || {};
  $("aside-title").firstChild.textContent = "Şu an ";
  $("aside-sub").textContent = isCodex ? "Codex" : "Claude Code";
  const rows = [];
  // projected EOD
  if (fc.today?.projected_eod != null) rows.push(["Gün sonu tahmini", fmtMoney0(fc.today.projected_eod)]);
  // 5h window remaining
  if (cw.active) {
    const m = Math.floor((cw.remaining_seconds || 0) / 60);
    rows.push(["5-saat pencere", (m >= 60 ? `${Math.floor(m / 60)}sa ${m % 60}dk` : `${m}dk`) + " kaldı"]);
  }
  // weekly cap (notional)
  const wc = rep.weekly_cap || {};
  if (wc.rolling_7d_cost != null) rows.push(["Bu hafta", fmtMoney0(wc.rolling_7d_cost) + (isCodex ? " (notional)" : "")]);
  // active models
  const live = (rep.live_active_models_by_window || {})["60"] || {};
  if (live.models && live.models.length) {
    const top = live.models[0];
    rows.push(["Aktif model", modelDisplay(top.model_id || top.model || "")]);
  }
  $("hero-aside-body").innerHTML = rows.map(([l, v]) =>
    `<div style="display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid var(--border-subtle)">
      <span style="color:var(--text-3);font-size:12px">${l}</span>
      <span class="num" style="color:var(--text-1);font-size:13px">${v}</span></div>`).join("")
    || `<div class="empty">Aktif pencere yok</div>`;
}

// ---------- KPIs ----------
function renderKPIs(rep, isCodex) {
  const fc = rep.forecast || {}, t = rep.totals || {}, ce = rep.cache_efficiency || {};
  const daily = rep.daily || [];
  const todayE = daily[daily.length - 1] || {};
  const burn = fc.burn_rate_per_hour_recent ?? 0;
  const kpis = [
    { l: "Bugün harcanan", v: fmtMoney(fc.today?.so_far ?? 0), s: `${fmtInt(todayE.messages || 0)} turn`, spark: daily.slice(-14).map(d => d.cost_usd || 0) },
    { l: "Burn rate", v: fmtMoney(burn), unit: "/sa", s: "son 1 saat tempo", spark: null },
    { l: "Cache tasarrufu", v: fmtMoney0(ce.usd_saved || 0), s: `%${Math.round((ce.discount_pct || 0) * 100)} indirim · lifetime`, spark: null },
    { l: "Lifetime $", v: fmtMoney0(t.cost_usd || 0), s: `${fmtCompact(t.total_tokens || 0)} token`, spark: daily.slice(-14).map(d => d.cost_usd || 0) },
  ];
  kpis.forEach((k, i) => {
    const n = i + 1;
    $(`kpi${n}-l`).textContent = k.l;
    $(`kpi${n}-v`).innerHTML = k.v + (k.unit ? `<small>${k.unit}</small>` : "");
    $(`kpi${n}-s`).textContent = k.s;
    drawSpark(`kpi${n}-spark`, k.spark);
  });
}

// ---------- plan limits (per-device meters) ----------
function renderLimits(rep, isCodex) {
  const title = $("limits-title");
  title.firstChild.textContent = "Plan limiti ";
  if (isCodex) {
    const rl = rep.codex_rate_limits || {};
    $("limits-sub").textContent = "Codex Pro · 5h + haftalık (gerçek kısıt)";
    const order = ["mac", "pc"].filter(d => rl[d]);
    if (!order.length) { $("limits-body").innerHTML = `<div class="empty">Rate-limit verisi yok</div>`; return; }
    $("limits-body").innerHTML = order.map(dev => {
      const d = rl[dev];
      const bar = (name, w) => {
        if (!w) return "";
        const p = Math.max(0, Math.min(100, w.used_percent || 0)), c = sevClass(p);
        return `<div class="meter-row"><span class="meter-name">${name}</span>
          <div class="meter-track"><div class="meter-fill ${c}" style="width:${p}%"></div></div>
          <span class="meter-pct ${c}">%${Math.round(p)}</span>
          <span class="meter-reset">${w.resets_at ? "↻ " + resetIn(w.resets_at) : ""}</span></div>`;
      };
      return `<div style="margin-bottom:14px"><div style="margin-bottom:6px"><span class="badge ${dev}">cihan-${dev}</span>
        <span class="dim" style="font-family:var(--font-mono);font-size:10px;margin-left:6px">${esc(d.limit_name || d.limit_id || "")}</span></div>
        ${bar("5 saat", d.primary)}${bar("haftalık", d.secondary)}</div>`;
    }).join("");
  } else {
    // Claude: 5h block + weekly cap as meters
    const cw = rep.current_window || {}, wc = rep.weekly_cap || {};
    $("limits-sub").textContent = "Claude · 5h blok + haftalık (P90/P95 proxy)";
    const blockUsed = cw.cost_usd || 0, p90 = cw.baseline_cost_p90 || 1;
    const blockPct = Math.min(100, blockUsed / p90 * 100);
    const wkPct = Math.min(100, wc.pct_of_personal_p95 || 0);
    const bc = sevClass(blockPct), wkc = sevClass(wkPct);
    $("limits-body").innerHTML =
      `<div class="meter-row"><span class="meter-name">5h blok</span>
        <div class="meter-track"><div class="meter-fill ${bc}" style="width:${blockPct}%"></div></div>
        <span class="meter-pct ${bc}">%${Math.round(blockPct)}</span>
        <span class="meter-reset">${cw.active ? "↻ " + Math.floor((cw.remaining_seconds || 0) / 60) + "dk" : ""}</span></div>
       <div class="meter-row"><span class="meter-name">haftalık</span>
        <div class="meter-track"><div class="meter-fill ${wkc}" style="width:${wkPct}%"></div></div>
        <span class="meter-pct ${wkc}">%${Math.round(wkPct)}</span>
        <span class="meter-reset"></span></div>` +
      (() => {
        const vk = cw.verdict_kind === "bad" ? "bad" : cw.verdict_kind === "warn" ? "warn" : "good";
        const proj = cw.projected_close_cost;
        const eta = cw.block_eta_p50_iso && cw.block_will_hit_p50
          ? ` · P50'ye ~${cw.block_minutes_to_p50}dk` : "";
        return `<div style="display:flex;justify-content:space-between;align-items:baseline;margin-top:14px;padding-top:12px;border-top:1px solid var(--border-subtle)">
          <div><div class="dim" style="font-size:11px;text-transform:uppercase;letter-spacing:.04em">blok projeksiyonu</div>
            <div class="num" style="font-size:17px;color:var(--text-1);margin-top:3px">${proj != null ? "~" + fmtMoney0(proj) + " kapanır" : "—"}</div></div>
          <div style="text-align:right"><div class="dim" style="font-size:11px;text-transform:uppercase;letter-spacing:.04em">tempo</div>
            <div class="num ${vk === "bad" ? "" : ""}" style="font-size:13px;color:var(--${vk});margin-top:3px">${esc(cw.verdict_label || "—")}${eta}</div></div>
        </div>
        <div class="dim" style="font-size:11px;margin-top:10px;line-height:1.5">Bu blokta ${fmtMoney(blockUsed)} · P50 ${fmtMoney(cw.baseline_cost_p50 || 0)} · P90 ${fmtMoney(p90)}. Claude rate-limit %'si API'de açık değil — kişisel P90/P95 baz alınır.</div>`;
      })();
  }
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
       <div><div class="num" style="font-size:18px;color:var(--good)">${fmtMoney0(ce.usd_saved || 0)}</div><div class="dim" style="font-size:11px">tasarruf</div></div>
       <div style="text-align:right"><div class="num" style="font-size:18px;color:var(--text-1)">${fmtMoney0(ce.usd_on_cache || 0)}</div><div class="dim" style="font-size:11px">cache okuma maliyeti</div></div>
     </div>
     <div class="dim" style="font-size:11px;margin-top:12px;line-height:1.5">Cache olmasaydı bu okumalar ${fmtMoney0(ce.full_equiv_usd || 0)} tutardı — %${Math.round((ce.discount_pct || 0) * 100)} indirim.</div>`;
}

// ---------- trend chart ----------
function renderTrend(rep, isCodex) {
  const tf = localStorage.getItem("ccmeter_trend_tf") || "24";
  $("trend-unit").textContent = "$/saat · " + (tf === "24" ? "1 gün" : tf + "h") + " pencere";
  const series = (rep.burn_rate_timeseries || {})[tf] || [];
  const shortWin = (tf === "1" || tf === "4" || tf === "6");
  const labels = series.map(p => {
    const dt = p.start ? new Date(p.start) : null;
    if (!dt) return "";
    return shortWin
      ? dt.toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit" })
      : (dt.getMonth() + 1) + "/" + dt.getDate();
  });
  const data = series.map(p => p.cost_per_hour ?? p.rate ?? p.value ?? p.cost ?? 0);
  const ctx = $("chart-trend");
  if (window.__charts.trend) window.__charts.trend.destroy();
  const grad = ctx.getContext("2d").createLinearGradient(0, 0, 0, 240);
  grad.addColorStop(0, "rgba(94,106,210,0.22)"); grad.addColorStop(1, "rgba(94,106,210,0)");
  window.__charts.trend = new Chart(ctx, {
    type: "line",
    data: { labels, datasets: [{ data, borderColor: "#5E6AD2", backgroundColor: grad, borderWidth: 2, fill: true, tension: 0.35, pointRadius: 0, pointHoverRadius: 4 }] },
    options: chartOpts({ yMoney: true })
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
    options: { responsive: true, maintainAspectRatio: false, cutout: "66%", plugins: { legend: { display: false }, tooltip: tooltipCfg() } }
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
  $("daily-sub").textContent = daily.length ? `toplam ${fmtMoney0(data.reduce((a, b) => a + b, 0))}` : "";
  const ctx = $("chart-daily");
  if (window.__charts.daily) window.__charts.daily.destroy();
  window.__charts.daily = new Chart(ctx, {
    type: "bar",
    data: { labels, datasets: [{ data, backgroundColor: colors, borderRadius: 3, maxBarThickness: 18 }] },
    options: chartOpts({ yMoney: true })
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
      return `<div class="bar-list-row"><span class="nm">${ERR_LABELS[c.category] || c.category}</span>
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
  const chrome = all.filter(t => /Chrome|chrome/.test(t.tool));
  const ctok = chrome.reduce((s, t) => s + (t.approx_output_tokens || 0), 0);
  $("tools-sub").textContent = `${fmtCompact(totalTok)} output token` + (ctok ? ` · MCP %${(ctok / totalTok * 100).toFixed(1)}` : "");
  const max = Math.max(...rows.map(r => r.tok), 1);
  $("tools-body").innerHTML = rows.map(r => {
    const short = r.tool.replace(/^mcp__/, "").replace(/__/g, ":").slice(0, 28);
    return `<div class="bar-list-row"><span class="nm" title="${esc(r.tool)}">${esc(short)}</span>
      <span class="tr"><i style="width:${r.tok / max * 100}%"></i></span>
      <span class="vl">${fmtInt(r.calls)}× · ${fmtCompact(r.tok)}t</span></div>`;
  }).join("");
}

// ---------- charts shared ----------
function tooltipCfg() {
  return { backgroundColor: "#1C1D1F", borderColor: "rgba(255,255,255,0.09)", borderWidth: 1, titleColor: "#F7F8F8", bodyColor: "#D0D6E0", padding: 10, cornerRadius: 8, displayColors: false, bodyFont: { family: "'Geist Mono',monospace" } };
}
function chartOpts({ yMoney }) {
  return {
    responsive: true, maintainAspectRatio: false,
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
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { enabled: false } }, scales: { x: { display: false }, y: { display: false } } }
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
  // trend picker
  const tf0 = localStorage.getItem("ccmeter_trend_tf") || "24";
  document.querySelectorAll("#trend-picker button").forEach(b => {
    b.classList.toggle("active", b.dataset.tf === tf0);
    b.addEventListener("click", () => {
      localStorage.setItem("ccmeter_trend_tf", b.dataset.tf);
      document.querySelectorAll("#trend-picker button").forEach(x => x.classList.toggle("active", x.dataset.tf === b.dataset.tf));
      if (window.__lastReport) renderTrend(window.__lastReport, window.__source === "codex");
    });
  });
  refresh(false);
  setInterval(() => refresh(false), 10000);
  setInterval(updateLiveStamp, 1000);
}
document.addEventListener("DOMContentLoaded", start);
