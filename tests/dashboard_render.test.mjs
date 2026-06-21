// Multi-device render invariants for dashboard.js — runs in plain Node (no framework).
//
// Why this exists: the combined "Claude + Codex" tab is rendered by code paths SEPARATE
// from the single tabs (bmScopedHalf vs bmScopedRep) and is repainted by several timers.
// Two shipped bugs lived exactly here: (1) bmScopedHalf ignored remote devices' burn,
// (2) liveGaugeTick repainted with raw local halves and clobbered the cross-device
// aggregate every 5s (the "correct then $0" flicker). These run only with 2+ devices —
// which can't be reproduced on one dev machine — so they slipped past the Python tests.
//
// This harness evaluates the real dashboard.js with stubbed browser globals, feeds it a
// 2-device fixture (PC runs Claude, Mac runs Codex; each idle on the other source), and
// asserts the combined tab equals the single tab at EVERY window, and that the gauge value
// is the cross-device aggregate (never local-only). It would have caught both bugs.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";

const __dirname = dirname(fileURLToPath(import.meta.url));
const JS_PATH = join(__dirname, "..", "burnmeter", "static", "dashboard.js");
const code = readFileSync(JS_PATH, "utf8");

// ---- stub browser globals so dashboard.js evaluates without a DOM ----
function stubEl() {
  return new Proxy(function () {}, {
    get(_t, k) {
      if (k === "classList") return { toggle() {}, add() {}, remove() {}, contains: () => false };
      if (k === "style") return {};
      if (k === "dataset") return {};
      if (k === "querySelectorAll") return () => [];
      if (k === "querySelector") return () => stubEl();
      if (k === "addEventListener" || k === "removeEventListener" || k === "appendChild") return () => {};
      if (k === "textContent" || k === "innerHTML" || k === "value") return "";
      return stubEl();
    },
    set() { return true; },
    apply() { return stubEl(); },
  });
}
const store = new Map();
const localStorageStub = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
  clear: () => store.clear(),
};
const documentStub = {
  addEventListener() {}, removeEventListener() {},
  getElementById: () => stubEl(), querySelector: () => stubEl(), querySelectorAll: () => [],
  documentElement: { style: { setProperty() {} } },
  title: "", body: stubEl(), createElement: () => stubEl(),
};
const windowStub = {
  addEventListener() {}, removeEventListener() {}, matchMedia: () => ({ matches: false, addEventListener() {} }),
  __charts: {}, __reportCache: {}, location: { href: "", reload() {} },
};

globalThis.window = windowStub;
globalThis.document = documentStub;
globalThis.localStorage = localStorageStub;
globalThis.fetch = () => Promise.resolve({ ok: false, json: () => Promise.resolve({}) });
globalThis.Chart = function () { return { destroy() {} }; };
globalThis.setInterval = () => 0;
globalThis.setTimeout = () => 0;
globalThis.requestAnimationFrame = () => 0;
globalThis.location = windowStub.location;

// Evaluate dashboard.js and capture the pure functions under test. Function declarations
// are local to the wrapper; the appended assignment captures them out.
const run = new Function(
  code + "\n; globalThis.__BM = { bmScopedHalf, bmScopedRep, bmDeviceAgg, bmDeviceAggFresh, liveBurnRate, bmPathParts };"
);
run();
const { bmScopedHalf, bmScopedRep, liveBurnRate, bmPathParts } = globalThis.__BM;

// ---- fixture: 2 devices. PC (this) runs Claude only; Mac runs Codex only. ----
const WINS = ["0.0833", "0.25", "1", "2", "4", "6"];
const macCodexBBH = { "0.0833": 21.58, "0.25": 18.0, "1": 14.15, "2": 10.48, "4": 6.91, "6": 6.5 };
const pcClaudeBBH = { "0.0833": 94.08, "0.25": 70.0, "1": 50.0, "2": 42.74, "4": 30.0, "6": 25.0 };
const zero = { "0.0833": 0, "0.25": 0, "1": 0, "2": 0, "4": 0, "6": 0 };

const mkLocal = (bbh, source) => ({
  _meta: { source, version: "test" },
  record_count: 1000,
  forecast: { burn_rates_by_hours: { ...bbh }, burn_rate_per_hour_recent: bbh["2"],
              recent_costs: [], today: { so_far: 1 }, month: { so_far: 10 } },
  totals: { cost_usd: 100, total_tokens: 1e6, cache_hit_rate: 0.9 },
  burn_rate_zones: { typical: 4, busy: 12, heavy: 25, max: 50 },
  recent_turns: [],
});
const mkSnap = (bbh) => ({
  burn_rates_by_hours: { ...bbh }, burn_rate_per_hour: bbh["2"],
  today_cost: 1, month_so_far: 10, lifetime_cost: 100, lifetime_tokens: 1e6,
  cache_hit_rate: 0.9, record_count: 1000, recent: [],
});
const devicesCache = {
  this_device: "PC",
  devices: [
    { device_id: "PC", label: "PC", os: "Windows", sources: { claude: mkSnap(pcClaudeBBH), codex: mkSnap(zero) } },
    { device_id: "MAC", label: "Mac", os: "Darwin", sources: { claude: mkSnap(zero), codex: mkSnap(macCodexBBH) } },
  ],
};
// Mac is actively running a Codex model (30s ago) + ran another 10min ago.
const recentISO = new Date(Date.now() - 30_000).toISOString();
const oldISO = new Date(Date.now() - 600_000).toISOString();
devicesCache.devices[1].sources.codex.live = [
  { model_id: "gpt-5.5", tokens_per_min: 28000, messages: 5, cost_per_hour: 24, last_seen: recentISO, project: "Decentralized_AI", project_dir: "/Users/cihan/dev/Decentralized_AI" },
  { model_id: "gpt-5.5-mini", tokens_per_min: 1000, messages: 1, cost_per_hour: 2, last_seen: oldISO, project: "Decentralized_AI", project_dir: "/Users/cihan/dev/Decentralized_AI" },
];
window.__devicesCache = devicesCache;

const clLocal = mkLocal(pcClaudeBBH, "claude");
const cxLocal = mkLocal(zero, "codex");
clLocal.live_active_models_by_window = {}; cxLocal.live_active_models_by_window = {};

let passed = 0;
const near = (a, b) => Math.abs(a - b) < 1e-6;

// === TEST 1: combined-tab burn == single-tab burn, every source, every window, scope=all ===
for (const [source, local, remoteBBH] of [["codex", cxLocal, macCodexBBH], ["claude", clLocal, pcClaudeBBH]]) {
  window.__scope = "all";
  window.__source = source;
  for (const W of WINS) {
    store.set("burnmeter_burn_hours_" + source, W);
    // single tab
    window.__lastReport = local;
    const single = bmScopedRep(local).forecast.burn_rate_per_hour_recent;
    // combined tab
    window.__lastReport = { _combined: true, claude: clLocal, codex: cxLocal };
    const half = bmScopedHalf(local, source);
    const combinedAgg = half.forecast.burn_rates_by_hours[W];
    const combinedGauge = liveBurnRate(half.forecast, W); // what the 5s tick + renderCombined paint
    assert.ok(near(single, combinedAgg),
      `combined==single mismatch [${source} ${W}]: single=${single} combinedAgg=${combinedAgg}`);
    assert.ok(near(combinedGauge, combinedAgg),
      `gauge!=aggregate [${source} ${W}]: gauge=${combinedGauge} agg=${combinedAgg} (liveBurnRate must read the aggregate, not local recent_costs)`);
    passed += 2;
  }
}

// === TEST 2: the exact flicker scenario — PC viewing combined Codex must show Mac's burn, NOT 0 ===
window.__scope = "all"; window.__source = "codex";
store.set("burnmeter_burn_hours_codex", "0.0833");
window.__lastReport = { _combined: true, claude: clLocal, codex: cxLocal };
const cxHalf = bmScopedHalf(cxLocal, "codex");
const cxGauge = liveBurnRate(cxHalf.forecast, "0.0833");
assert.ok(near(cxGauge, macCodexBBH["0.0833"]),
  `FLICKER REGRESSION: PC combined Codex 5m gauge=${cxGauge}, expected Mac's ${macCodexBBH["0.0833"]} (must NOT be local-only 0)`);
assert.ok(cxHalf.forecast.recent_costs === null,
  "bmScopedHalf must null recent_costs so liveBurnRate uses the cross-device aggregate");
passed += 2;

// === TEST 3: scope = a specific remote device shows THAT device's burn ===
window.__scope = "MAC"; window.__source = "codex";
store.set("burnmeter_burn_hours_codex", "1");
const macOnly = bmScopedHalf(cxLocal, "codex");
assert.ok(near(macOnly.forecast.burn_rates_by_hours["1"], macCodexBBH["1"]),
  `remote-device scope wrong: got ${macOnly.forecast.burn_rates_by_hours["1"]} expected ${macCodexBBH["1"]}`);
passed += 1;

// === TEST 4: static guard — periodic gauge repaint must NOT use raw report halves ===
const lgt = code.slice(code.indexOf("function liveGaugeTick"), code.indexOf("function liveGaugeTick") + 700);
assert.ok(/bmScopedHalf\(\s*rep\.claude/.test(lgt) && /bmScopedHalf\(\s*rep\.codex/.test(lgt),
  "liveGaugeTick must repaint the combined gauge via bmScopedHalf(rep.claude/codex), not raw halves");
assert.ok(!/paintBurnGauge\([^)]*\},\s*rep\.(claude|codex),/.test(lgt),
  "liveGaugeTick must not pass a raw report half straight to paintBurnGauge");
passed += 2;

// === TEST 5: combined Codex live panel shows the Mac's RUNNING model (cross-device) ===
window.__scope = "all"; window.__source = "codex";
window.__lastReport = { _combined: true, claude: clLocal, codex: cxLocal };
const cxHalf2 = bmScopedHalf(cxLocal, "codex");
const live5 = ((cxHalf2.live_active_models_by_window || {})["5"] || {}).models || [];
const macRunning = live5.find((m) => m.device === "Mac" && m.model_id === "gpt-5.5");
assert.ok(macRunning, "combined Codex 'Live active model' (5m) must include the Mac's running model");
assert.ok(macRunning.seconds_since_last <= 300, `recomputed seconds_since_last out of window: ${macRunning?.seconds_since_last}`);
// the 10-minute-old model must NOT appear in the 5m window, but SHOULD in 15m
const stale5 = live5.find((m) => m.model_id === "gpt-5.5-mini");
assert.ok(!stale5, "a 10m-old model must not show in the 5m live window");
const live15 = ((cxHalf2.live_active_models_by_window || {})["15"] || {}).models || [];
assert.ok(live15.find((m) => m.model_id === "gpt-5.5-mini"), "a 10m-old model SHOULD show in the 15m window");
passed += 3;

// === TEST 6: single-tab scoped rep (bmScopedRep) also carries cross-device live models ===
window.__scope = "all"; window.__source = "codex";
window.__lastReport = cxLocal;
const sr = bmScopedRep(cxLocal);
const sr5 = ((sr.live_active_models_by_window || {})["5"] || {}).models || [];
assert.ok(sr5.find((m) => m.device === "Mac" && m.model_id === "gpt-5.5"),
  "single-tab scoped rep must also include the Mac's running model (refresh/picker paths use it)");
passed += 1;

// === TEST 7: bmPathParts splits dir into folder name + compact path + full (hover) ===
const pp1 = bmPathParts("/Users/cihan/dev/BURNMETER/repo", "repo");
assert.equal(pp1.name, "repo", "name = basename");
assert.equal(pp1.sub, "…/dev/BURNMETER", `sub should be last-2 parent segments, got ${pp1.sub}`);
assert.equal(pp1.full, "/Users/cihan/dev/BURNMETER/repo", "full = whole path for hover");
const pp2 = bmPathParts("C:\\work\\repo", "repo");
assert.equal(pp2.sub, "C:/work", `backslash parent (last-2 segments), got ${pp2.sub}`);
const pp3 = bmPathParts("", "repo");
assert.ok(pp3.name === "repo" && pp3.sub === "", "empty dir → name only, no sub");
const pp4 = bmPathParts(null, null);
assert.equal(pp4.name, "unknown", "null dir+label → 'unknown', no throw");
passed += 5;

// === TEST 8: project_dir flows through the live-model merge (path shown cross-device) ===
window.__scope = "all"; window.__source = "codex";
window.__lastReport = { _combined: true, claude: clLocal, codex: cxLocal };
const cxHalf3 = bmScopedHalf(cxLocal, "codex");
const macRun = (((cxHalf3.live_active_models_by_window || {})["5"] || {}).models || []).find((m) => m.device === "Mac");
assert.ok(macRun && macRun.project_dir === "/Users/cihan/dev/Decentralized_AI",
  `remote live model must carry project_dir, got ${macRun && macRun.project_dir}`);
passed += 1;

console.log(`dashboard_render: ${passed} assertions passed`);
