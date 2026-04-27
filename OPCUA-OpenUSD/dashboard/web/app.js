// Live dashboard frontend.
// Connects WebSocket to the backend and renders gauges + chart.
// All DOM construction uses createElement / textContent — no innerHTML.

const SVG_NS = "http://www.w3.org/2000/svg";

const AXIS_COLORS = {
  1: "#3b82f6", 2: "#22c55e", 3: "#eab308",
  4: "#3b82f6", 5: "#22c55e", 6: "#eab308",
};
const TEMP_COLORS = {
  1: "#3b82f6", 2: "#22c55e", 3: "#eab308",
  4: "#ef4444", 5: "#a855f7", 6: "#06b6d4",
};

function el(tag, props, children) {
  const e = document.createElement(tag);
  if (props) for (const [k, v] of Object.entries(props)) {
    if (k === "className") e.className = v;
    else if (k === "style") Object.assign(e.style, v);
    else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  for (const c of (children || [])) {
    if (c == null) continue;
    e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return e;
}
function svg(tag, attrs, children) {
  const e = document.createElementNS(SVG_NS, tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  for (const c of (children || [])) e.appendChild(c);
  return e;
}

// ─────── Build six gauges ───────
const gaugesEl = document.getElementById("gauges");
const gaugeNodes = {};
for (let i = 1; i <= 6; i++) {
  const color = AXIS_COLORS[i];
  const grad = svg("linearGradient", { id: `grad-${i}`, x1: 0, x2: 1, y1: 0, y2: 0 }, [
    svg("stop", { offset: "0%", "stop-color": color, "stop-opacity": "0.4" }),
    svg("stop", { offset: "100%", "stop-color": color, "stop-opacity": "1" }),
  ]);
  const filterFe = svg("filter", { id: `glow-${i}`, x: "-20%", y: "-20%", width: "140%", height: "140%" }, [
    svg("feGaussianBlur", { stdDeviation: "2", result: "blur" }),
    svg("feMerge", {}, [svg("feMergeNode", { in: "blur" }), svg("feMergeNode", { in: "SourceGraphic" })]),
  ]);
  const defs = svg("defs", {}, [grad, filterFe]);
  const trackPath = svg("path", { d: "M20 95 A 80 80 0 0 1 180 95", stroke: "#1d2230", "stroke-width": "6", fill: "none" });
  const arc = svg("path", {
    id: `arc-${i}`, d: "M20 95 A 80 80 0 0 1 180 95",
    stroke: `url(#grad-${i})`, "stroke-width": "6", fill: "none",
    "stroke-linecap": "round", filter: `url(#glow-${i})`,
    pathLength: "100", "stroke-dasharray": "0 100",
  });
  const needle = svg("circle", { id: `needle-${i}`, cx: 20, cy: 95, r: 4, fill: color, filter: `url(#glow-${i})` });
  const svgEl = svg("svg", { class: "gauge-svg", viewBox: "0 0 200 110", preserveAspectRatio: "xMidYMid meet" },
    [defs, trackPath, arc, needle]);

  const valEl = el("div", { className: "gauge-value", id: `val-${i}` }, ["—°"]);
  const wrap = el("div", { className: "gauge" }, [
    el("div", { className: "gauge-label" }, [`Axis ${i}`]),
    svgEl,
    valEl,
    el("div", { className: "gauge-axis-name", style: { color } }, [`Axis ${i}`]),
    el("div", { className: "gauge-bounds" }, [
      el("span", null, ["-180°"]),
      el("span", null, ["180°"]),
    ]),
  ]);
  gaugesEl.appendChild(wrap);
  gaugeNodes[i] = { arc, needle, val: valEl };
}

function updateGauge(axis, deg) {
  const clamped = Math.max(-180, Math.min(180, deg));
  const pct = (clamped + 180) / 360;
  const node = gaugeNodes[axis];
  if (!node) return;
  node.arc.setAttribute("stroke-dasharray", `${pct * 100} 100`);
  node.val.textContent = `${clamped.toFixed(2)}°`;
  // Move the needle along the arc. Arc center: (100, 95), radius 80.
  const angle = Math.PI * (1 - pct);
  const cx = 100 + Math.cos(angle) * 80;
  const cy = 95 - Math.sin(angle) * 80;
  node.needle.setAttribute("cx", cx);
  node.needle.setAttribute("cy", cy);
}

// ─────── Chart ───────
const chartCanvas = document.getElementById("temp-chart");
const tempChart = new Chart(chartCanvas, {
  type: "line",
  data: {
    labels: [],
    datasets: [1, 2, 3, 4, 5, 6].map(i => ({
      label: `Axis ${i}`,
      data: [],
      borderColor: TEMP_COLORS[i],
      backgroundColor: TEMP_COLORS[i] + "22",
      borderWidth: 2,
      tension: 0.35,
      pointRadius: 0,
      pointHoverRadius: 3,
    })),
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: "nearest", intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: "#11151f", borderColor: "#1f2533", borderWidth: 1,
        titleColor: "#e6e9ef", bodyColor: "#e6e9ef",
      },
    },
    scales: {
      x: { ticks: { color: "#6f7589", font: { size: 10 } }, grid: { color: "#1d2230" } },
      y: { min: 0, max: 110, ticks: { color: "#6f7589", font: { size: 10 } }, grid: { color: "#1d2230" } },
    },
  },
});

// Render the legend.
const legendEl = document.getElementById("legend");
for (let i = 1; i <= 6; i++) {
  legendEl.appendChild(
    el("span", { style: { color: TEMP_COLORS[i] } }, [`Axis ${i}`])
  );
}

// ─────── State helpers ───────
function setConn(state, msg) {
  const pill = document.getElementById("conn-pill");
  const dot = document.getElementById("conn-dot");
  const status = document.getElementById("conn-status");
  pill.classList.remove("ok", "warn", "err");
  dot.classList.remove("dot-live", "dot-warn", "dot-err");
  const titleEl = pill.querySelector(".conn-pill-title");
  const msgEl = pill.querySelector(".conn-pill-msg");
  if (state === "ok") {
    pill.classList.add("ok");
    dot.classList.add("dot-live");
    titleEl.textContent = "CONNECTED";
    msgEl.textContent = msg || "Live data flowing.";
    status.textContent = "Live Monitoring";
  } else if (state === "warn") {
    pill.classList.add("warn");
    dot.classList.add("dot-warn");
    titleEl.textContent = "RECONNECTING";
    msgEl.textContent = msg || "Re-establishing link…";
    status.textContent = "Reconnecting";
  } else {
    pill.classList.add("err");
    dot.classList.add("dot-err");
    titleEl.textContent = "DISCONNECTED";
    msgEl.textContent = msg || "OPC UA link down.";
    status.textContent = "Offline";
  }
}

// ─────── WebSocket ───────
let ws = null;
let backoff = 500;

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const base = location.pathname.replace(/\/$/, "");
  const url = `${proto}://${location.host}${base}/ws`;
  ws = new WebSocket(url);

  ws.onopen = () => {
    setConn("ok", "Connection re-established.");
    backoff = 500;
  };
  ws.onmessage = (ev) => {
    try {
      const m = JSON.parse(ev.data);
      if (m.type === "snapshot") applySnapshot(m.data);
    } catch (_) { /* ignore */ }
  };
  ws.onclose = () => {
    setConn("warn", "Reconnecting…");
    setTimeout(connect, backoff);
    backoff = Math.min(backoff * 2, 5000);
  };
  ws.onerror = () => { try { ws.close(); } catch (_) {} };
}

const tempSeries = { 1: [], 2: [], 3: [], 4: [], 5: [], 6: [] };
const MAX_POINTS = 240;

function applySnapshot(snap) {
  if (!snap.connected) setConn("err", "OPC UA backend down.");

  let mn = +Infinity, mx = -Infinity, sum = 0, n = 0;
  for (let i = 1; i <= 6; i++) {
    const ax = snap.axes[i] || snap.axes[String(i)];
    if (!ax) continue;
    updateGauge(i, ax.position);
    if (ax.position < mn) mn = ax.position;
    if (ax.position > mx) mx = ax.position;
    sum += Math.abs(ax.position);
    n++;
    const series = tempSeries[i];
    series.push(ax.motor_temp);
    if (series.length > MAX_POINTS) series.shift();
  }

  document.getElementById("sum-min").textContent = isFinite(mn) ? mn.toFixed(2) + "°" : "—";
  document.getElementById("sum-max").textContent = isFinite(mx) ? mx.toFixed(2) + "°" : "—";
  document.getElementById("sum-avg").textContent = n ? (sum / n).toFixed(2) + "°" : "—";

  const psLabel = snap.program_state_label || "Unknown";
  const psEl = document.getElementById("ps-label");
  const psTag = document.getElementById("ps-tag");
  psEl.textContent = psLabel;
  psEl.classList.remove("warn", "err");
  if (snap.program_state === 6 || snap.program_state === 5) {
    psEl.classList.add("err");
    psTag.textContent = snap.program_state === 6 ? "Maintenance required" : "Aborted";
  } else if (snap.program_state === 3 || snap.program_state === 4) {
    psEl.classList.add("warn");
    psTag.textContent = "Stopping / stopped";
  } else {
    psTag.textContent = "All systems normal";
  }

  document.getElementById("perf-cycle").textContent = "1.20 s";
  document.getElementById("perf-throughput").textContent = String(snap.cycle_counter * 12);
  const maxMotor = Math.max(...Object.values(snap.axes).map(a => a.motor_temp));
  const eff = Math.max(20, 100 - Math.max(0, (maxMotor - 30)) * 0.8);
  document.getElementById("perf-eff").textContent = eff.toFixed(1) + "%";

  const overheats = Object.entries(snap.axes).filter(([, a]) => a.motor_temp >= 90).length;
  document.getElementById("sum-alerts").textContent = String(overheats);
  const alertsState = document.getElementById("alerts-state");
  const alertsTag = document.getElementById("alerts-tag");
  if (overheats > 0) {
    alertsState.textContent = `${overheats} active alert${overheats > 1 ? "s" : ""}`;
    alertsState.style.color = "var(--red)";
    alertsTag.textContent = "Motor overheat threshold exceeded";
  } else {
    alertsState.textContent = "No active alerts";
    alertsState.style.color = "var(--green)";
    alertsTag.textContent = "All clear";
  }

  const health = Math.max(40, 100 - overheats * 10);
  document.getElementById("health-value").textContent = String(health);
  const arc = document.getElementById("health-arc");
  const circumference = 2 * Math.PI * 44;
  arc.setAttribute("stroke-dashoffset", String(circumference * (1 - health / 100)));
  arc.setAttribute("stroke", health > 80 ? "#22c55e" : health > 60 ? "#eab308" : "#ef4444");

  const recoRow = document.getElementById("reco-row");
  if (snap.active_recommendation && snap.active_recommendation.title) {
    recoRow.style.display = "";
    document.getElementById("reco-title").textContent = snap.active_recommendation.title;
    document.getElementById("reco-cite").textContent =
      "Spec citation: " + (snap.active_recommendation.spec_citation || "—");
    document.getElementById("reco-rationale").textContent =
      snap.active_recommendation.rationale || "";
  } else {
    recoRow.style.display = "none";
  }

  if (snap.connected_since) {
    const up = Math.max(0, Math.floor(Date.now() / 1000 - snap.connected_since));
    const h = Math.floor(up / 3600);
    const m = Math.floor((up % 3600) / 60);
    const s = up % 60;
    document.getElementById("uptime").textContent =
      h ? `${h}h ${m}m ${s}s` : `${m}m ${s}s`;
  }

  if (!applySnapshot._lastChart || snap.ts_ms - applySnapshot._lastChart > 950) {
    applySnapshot._lastChart = snap.ts_ms;
    tempChart.data.labels = tempSeries[1].map((_, i) =>
      new Date(snap.ts_ms - (tempSeries[1].length - i) * 250)
        .toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false })
    );
    for (let i = 1; i <= 6; i++) tempChart.data.datasets[i - 1].data = [...tempSeries[i]];
    tempChart.update("none");
  }
}

// ─────── Clock ───────
function tickClock() {
  const now = new Date();
  document.getElementById("now-time").textContent = now.toLocaleTimeString("en-GB", { hour12: false });
  document.getElementById("now-date").textContent = now.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
}
setInterval(tickClock, 1000);
tickClock();

// ─────── Buttons ───────
function basePath() {
  return location.pathname.replace(/\/$/, "");
}
document.getElementById("inject-btn").addEventListener("click", async () => {
  await fetch(`${basePath()}/api/inject-anomaly`, { method: "POST" });
});
document.getElementById("reset-btn").addEventListener("click", async (ev) => {
  // Visual confirmation; the WebSocket snapshot will reflect the live state
  // (positions starting to move, state going back to Running) within a beat.
  const btn = ev.currentTarget;
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Resetting…";
  try {
    const r = await fetch(`${basePath()}/api/reset`, { method: "POST" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
  } catch (e) {
    btn.textContent = `Failed: ${e.message || e}`;
  } finally {
    setTimeout(() => { btn.disabled = false; btn.textContent = original; }, 1500);
  }
});

// ─────── Ask the Spec ───────
const askThread = document.getElementById("ask-thread");
const askForm = document.getElementById("ask-form");
const askInput = document.getElementById("ask-input");
const askBtn = document.getElementById("ask-btn");

function renderQuestion(q) {
  return el("div", { className: "ask-msg-q" }, [q]);
}
function renderAnswer(text, citations) {
  const a = el("div", { className: "ask-msg-a" }, [text]);
  if (citations && citations.length) {
    const wrap = el("div", { className: "ask-cites" });
    citations.forEach(c => {
      const cite = el("div", { className: "ask-cite" });
      const tag = el("strong", null, [`[${c.part}#${(c.chunk_id || "").slice(0, 8)}]`]);
      cite.appendChild(tag);
      cite.appendChild(document.createTextNode(" " + (c.snippet || "").slice(0, 220)));
      wrap.appendChild(cite);
    });
    a.appendChild(wrap);
  }
  return a;
}

// `/spec` is the Traefik-stripped path that proxies to the rag-mcp service.
// It's cross-route from `/dashboard/`, so the URL must stay absolute.
const SPEC_URL = "/spec/api/specification/query";
const SPEC_TIMEOUT_MS = 90_000;

async function askSpec(question) {
  if (!question || !question.trim()) return;
  const q = question.trim();
  // Drop the empty hint on first ask.
  const empty = askThread.querySelector(".ask-empty");
  if (empty) empty.remove();

  askThread.appendChild(renderQuestion(q));
  const aHolder = el("div", { className: "ask-msg-a ask-pending" }, [
    el("span", { className: "spinner" }),
    el("span", { className: "ask-status" }, ["Asking the spec… 0.0 s"]),
  ]);
  askThread.appendChild(aHolder);
  askThread.scrollTop = askThread.scrollHeight;

  askBtn.disabled = true;
  askInput.disabled = true;
  const t0 = performance.now();
  const status = aHolder.querySelector(".ask-status");
  const tick = setInterval(() => {
    const dt = ((performance.now() - t0) / 1000).toFixed(1);
    if (status) status.textContent = `Asking the spec… ${dt} s`;
  }, 200);

  // Hard timeout — never let the panel look hung.
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort("timeout"), SPEC_TIMEOUT_MS);

  try {
    const r = await fetch(SPEC_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q, k: 4 }),
      signal: ctrl.signal,
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    aHolder.classList.remove("ask-pending");
    aHolder.replaceChildren();
    aHolder.appendChild(document.createTextNode(d.answer || "(no answer)"));
    if (d.citations && d.citations.length) {
      const wrap = el("div", { className: "ask-cites" });
      d.citations.forEach(c => {
        const cite = el("div", { className: "ask-cite" });
        cite.appendChild(el("strong", null, [`[${c.part}#${(c.chunk_id || "").slice(0, 8)}]`]));
        cite.appendChild(document.createTextNode(" " + (c.snippet || "").slice(0, 220)));
        wrap.appendChild(cite);
      });
      aHolder.appendChild(wrap);
    }
  } catch (e) {
    aHolder.classList.remove("ask-pending");
    const why = (e && e.name === "AbortError")
      ? `No answer after ${SPEC_TIMEOUT_MS / 1000} s — the model is overloaded. Try again or simplify the question.`
      : `Error: ${e && e.message ? e.message : e}`;
    aHolder.replaceChildren(document.createTextNode(why));
  } finally {
    clearTimeout(timer);
    clearInterval(tick);
    askBtn.disabled = false;
    askInput.disabled = false;
    askInput.value = "";
    askInput.focus();
    askThread.scrollTop = askThread.scrollHeight;
  }
}

askForm.addEventListener("submit", (e) => {
  e.preventDefault();
  askSpec(askInput.value);
});

// Click the example chips to auto-submit them. Guard against double-submits
// while a previous request is still in flight (askBtn is disabled during it).
askThread.addEventListener("click", (e) => {
  const t = e.target;
  if (t && t.tagName === "EM" && !askBtn.disabled) {
    askSpec(t.textContent.replace(/^"|"$/g, ""));
  }
});
async function decideRecommendation(approved) {
  // Immediate visual feedback — the WebSocket snapshot will reflect the
  // real state shortly, but the user gets a response within one frame.
  const approveBtn = document.getElementById("approve-btn");
  const rejectBtn = document.getElementById("reject-btn");
  const label = approved ? "Approving…" : "Rejecting…";
  approveBtn.disabled = true;
  rejectBtn.disabled = true;
  const target = approved ? approveBtn : rejectBtn;
  const original = target.textContent;
  target.textContent = label;
  let failed = false;
  try {
    const r = await fetch(
      `${basePath()}/api/approve?approved=${approved}`,
      { method: "POST" },
    );
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
  } catch (e) {
    failed = true;
    target.textContent = `Failed: ${e.message || e}`;
  } finally {
    // Re-enable after a short beat (longer when the call failed, so the
    // user actually has time to read the error before the label resets).
    const restoreMs = failed ? 4000 : 1500;
    setTimeout(() => {
      approveBtn.disabled = false;
      rejectBtn.disabled = false;
      target.textContent = original;
    }, restoreMs);
  }
}
document.getElementById("approve-btn").addEventListener("click", () => decideRecommendation(true));
document.getElementById("reject-btn").addEventListener("click", () => decideRecommendation(false));

connect();
