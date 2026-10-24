"use strict";
const TABS = [["24h", "24 hrs"], ["7d", "prev week"], ["30d", "prev month"], ["all", "all"]];
let range = localStorage.reportRange || "24h";
// stale/garbage saved keys (older deployment, manual junk) would crash render
if (!TABS.some(t => t[0] === range)) { range = "24h"; delete localStorage.reportRange; }
let data = null;
let srcUrl = null;

// ---- data ----
async function loadData() {
  const extra = new URLSearchParams(location.search).get("src");
  let urls = srcUrl ? [srcUrl] : ["report-data.json", "/report/data"];
  if (extra) urls = [extra, ...urls];
  for (const u of urls) {
    try {
      const r = await fetch(u, { cache: "no-store" });
      if (!r.ok) continue;
      const j = await r.json();
      if (j && Array.isArray(j.logs) && Array.isArray(j.times)) {
        srcUrl = u;
        return j;
      }
    } catch {}
    if (srcUrl) { srcUrl = null; return loadData(); }
  }
  return null;
}

// ---- time helpers ----
function plusMonth(t) { // t in seconds, calendar month add with day clamp
  const d = new Date(t * 1000);
  const day = d.getUTCDate();
  d.setUTCMonth(d.getUTCMonth() + 1);
  if (d.getUTCDate() !== day) d.setUTCDate(0);
  return d.getTime() / 1000;
}

function fmtRel(now, t) { // "2mo 11d", "3h 40m", "5m", "now"
  if (t >= now) return "now";
  let cur = t, months = 0;
  while (plusMonth(cur) <= now) { months++; cur = plusMonth(cur); }
  let s = now - cur;
  const units = [[months, "mo"], [Math.floor(s / 86400), "d"], [Math.floor(s % 86400 / 3600), "h"], [Math.floor(s % 3600 / 60), "m"]];
  const parts = units.filter(u => u[0] > 0).slice(0, 2).map(u => u[0] + u[1]);
  return parts.length ? parts.join(" ") : "now";
}

const pad = n => String(n).padStart(2, "0");
const fmtHr = t => { const d = new Date(t * 1000), h = d.getUTCHours(); return (h % 12 || 12) + (h < 12 ? "am" : "pm"); };
const fmtDate = t => { const d = new Date(t * 1000); return pad(d.getUTCMonth() + 1) + "-" + pad(d.getUTCDate()); };
const fmtUptime = s => `${Math.floor(s / 86400)}d ${pad(Math.floor(s % 86400 / 3600))}:${pad(Math.floor(s % 3600 / 60))}:${pad(Math.floor(s % 60))}`;
const fmtDateTime = t => {
  const d = new Date(t * 1000);
  return d.getUTCFullYear() + "-" + pad(d.getUTCMonth() + 1) + "-" + pad(d.getUTCDate())
    + " " + pad(d.getUTCHours()) + ":" + pad(d.getUTCMinutes()) + ":" + pad(d.getUTCSeconds());
};
const esc = s => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const parseCode = m => (m.match(/^solve (\d{3})/) || [])[1] || null;
const dspRel = (now, t) => { const r = fmtRel(now, t); return r === "now" ? "just now" : r + " ago"; };

// ---- timeline ----
function granularity(range, uptime) {
  switch (range) {
    case "24h": return [3600, 24];
    case "7d": return [21600, 28];
    case "30d": return [86400, 30];
    default: {
      if (uptime <= 7 * 86400) return [3600, Math.max(1, Math.floor(uptime / 3600)) + 1];
      const days = Math.floor(uptime / 86400) + 1;
      return days <= 240 ? [86400, days] : [Math.floor(uptime / 240) + 1, 240];
    }
  }
}

// dot raster: every request is one dot at its exact time, stacked bottom-up in
// 6px columns, so density reads as height; hover gives the request's timestamp
function dotSvg(times, now, span) {
  const W = 660, RIGHT = 650, X0 = 46, TOP = 8, BH = 80, H = 104, D = 6;
  const plot = RIGHT - X0;
  const cols = Math.floor(plot / D);
  const colSecs = span / cols;
  const stacks = new Array(cols).fill(0);
  let busiest = 0;
  let out = `<svg viewBox="0 0 ${W} ${H}">`;
  const sorted = times.slice().sort((a, b) => a - b);
  for (const t of sorted) {
    const raw = (t - (now - span)) / colSecs;
    if (!(raw >= 0 && raw <= cols)) continue; // outside range (NaN-safe)
    const col = Math.min(cols - 1, Math.floor(raw));
    const k = stacks[col]++;
    if (k + 1 > busiest) busiest = k + 1;
    const cy = Math.max(TOP + D / 2, TOP + BH - k * D - D / 2);
    out += `<circle cx="${(X0 + col * D + D / 2).toFixed(1)}" cy="${cy.toFixed(1)}" r="2" class="dotq"><title>${fmtDateTime(t)}</title></circle>`;
  }
  const tick = frac => {
    const t = now - frac * span;
    return span >= 2 * 86400 ? fmtDate(t) : fmtHr(t);
  };
  for (let j = 0; j <= 6; j++) {
    const frac = j / 6;
    out += `<text class="ax" x="${(X0 + plot * (1 - frac)).toFixed(1)}" y="${TOP + BH}" dy="14" text-anchor="${j ? "middle" : "end"}">${tick(frac)}</text>`;
  }
  return [out + "</svg>", busiest];
}

// ---- render ----
function render() {
  const d = data;
  // client clock behind the server would swallow newest dots (raw > cols);
  // server time is the anchor, client time only nudges forward
  const now = Math.max(Math.floor(Date.now() / 1000), d.now ?? 0);
  document.getElementById("k-uptime").textContent = fmtUptime(d.uptime);
  document.getElementById("k-served").textContent = d.served;
  document.getElementById("k-failed").textContent = d.failed;
  document.getElementById("k-date").textContent = fmtDateTime(d.now);

  const tabs = document.getElementById("range");
  tabs.innerHTML = TABS.map(([k, lbl]) => `<a data-range="${k}" class="${k === range ? "active" : ""}">${lbl}</a>`).join("");
  for (const a of tabs.children)
    a.onclick = () => { range = a.dataset.range; localStorage.reportRange = range; render(); };

  const [secsPer, n] = granularity(range, d.uptime);
  const span = secsPer * n;
  const [svg, max] = dotSvg(d.times, now, span);
  const last = d.times.length ? d.times[d.times.length - 1] : null;
  document.getElementById("cap").textContent =
    `requests \u00b7 ${TABS.find(t => t[0] === range)[1]} \u00b7 busiest ${max} \u00b7 ` +
    (last ? `last request ${dspRel(now, last)}` : "no requests yet");
  document.getElementById("chart").innerHTML = svg;

  document.getElementById("logs").innerHTML = d.logs.length
    ? d.logs.slice().sort((a, b) => b[0] - a[0]).map(([t, m]) => {
        const code = parseCode(m);
        const pill = code ? `<span class="code s${code[0]}">${code}</span>` : "";
        return `<li><span class="rel">${esc(dspRel(now, t))}</span><span class="msg">${esc(m)}</span>${pill}</li>`;
      }).join("")
    : '<li class="dim">nothing yet</li>';
}

async function tick() {
  const d = await loadData();
  if (d) { data = d; render(); }
  else document.getElementById("cap").textContent = "data source unreachable";
}
tick();
setInterval(tick, 5000);

// ---- selftest: open with ?selftest ----
if (location.search.includes("selftest")) {
  const fails = [];
  const A = (c, m) => { if (!c) fails.push(m); console.log(c ? "PASS" : "FAIL", m); };
  const now = daysFuel(2026, 3, 15);
  function daysFuel(y, m, d) { return Date.UTC(y, m - 1, d) / 1000; }
  A(dspRel(now, now) === "just now", "rel now → just now");
  A(dspRel(now, now - 90 * 60) === "1h 30m ago", "ago wording");
  A(dspRel(now, now + 10) === "just now", "rel future");
  A(parseCode("solve 200 (6 chars)") === "200", "code 200");
  A(parseCode("solve 413 262144B") === "413", "code 413");
  A(parseCode("api started") === null, "no code line");
  const [dots] = dotSvg([now - 10, now - 10, now - 43200], now, 86400);
  A((dots.match(/<circle/g) || []).length === 3, "one dot per request");
  A(dots.includes(`<title>${fmtDateTime(now - 10)}</title>`), "dot hover timestamp");
  A(dots.includes('text-anchor="end"'), "axis ticks");
  A(/(am|pm)/.test(dots), "hour labels");
  A(fmtRel(now, now - 4 * 86400 - 3 * 3600) === "4d 3h", "rel 4d3h");
  A(fmtRel(now, now - 3720) === "1h 2m", "rel 62min");
  A(fmtRel(now, daysFuel(2026, 1, 31)) === "1mo 15d", "rel months");
  const t = daysFuel(2026, 1, 31);
  const pm = new Date(plusMonth(t) * 1000);
  A(pm.toISOString().slice(0, 10) === "2026-02-28", "plusMonth clamp");
  const [sp, cnt] = granularity("24h", 3 * 3600);
  A(sp === 3600 && cnt === 24, "granularity 24h fixed");
  const g = granularity("all", 2 * 3600);
  A(g[0] === 3600 && g[1] === 3, "granularity all hourly");
  const el = document.createElement("p");
  el.textContent = fails.length ? `selftest FAIL: ${fails.length}` : "selftest OK";
  el.style.cssText = "color:" + (fails.length ? "#f33" : "#3f3") + ";padding:.5rem";
  document.querySelector(".card").prepend(el);
}
