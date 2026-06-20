/* Liqhunt Cascade Radar — D3 frontend. */
"use strict";

// ---- pure helpers (console-testable) ----
const WINDOW_MS = 5 * 60 * 1000;       // 5 min visible
const MAX_BLIPS = 600;

function usdToRadius(usd) {            // log-scaled blip radius
  return Math.max(2, Math.min(28, 2 + Math.log10(Math.max(usd, 1)) * 3));
}
function ageAlpha(ageMs) {             // fade as event ages out of window
  return Math.max(0, 1 - ageMs / WINDOW_MS);
}
function makePriceScale(centerPrice, halfSpan, height) {
  return d3.scaleLinear()
    .domain([centerPrice - halfSpan, centerPrice + halfSpan])
    .range([height, 0]);
}

// ---- state ----
const state = {
  price: 0, magnets: { long: [], short: [] }, signal: null, verdict: "none",
  blips: [],                 // {ts, side, usd, price}
  priceTrace: [],            // {ts, price}
  intensity: [],             // {ts, side, usd}
  frozen: false,
};

// ---- DOM ----
const fieldSvg = d3.select("#field");
const intSvg = d3.select("#intensity");
const elPrice = document.getElementById("price");
const elVerdict = document.getElementById("verdict");

function setText(id, v) { document.querySelector(`#${id} b`).textContent = v; }

// ---- websocket ----
function connect() {
  const ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type === "liq") onLiq(m);
    else if (m.type === "state") onState(m);
  };
  ws.onclose = () => setTimeout(connect, 1500);
}

function onLiq(m) {
  state.blips.push(m);
  state.intensity.push({ ts: m.ts * 1000, side: m.side, usd: m.usd });
  if (state.blips.length > MAX_BLIPS) state.blips.shift();
}

function onState(s) {
  state.price = s.price;
  state.magnets = s.magnets;
  state.verdict = s.verdict;
  state.signal = s.signal;
  if (s.price > 0) state.priceTrace.push({ ts: Date.now(), price: s.price });

  elPrice.textContent = `BTC $${Math.round(s.price).toLocaleString()}`;
  if (s.verdict === "ARMED" && s.signal) {
    elVerdict.className = "armed";
    elVerdict.textContent =
      `ARMED ${s.signal.direction} → ${Math.round(s.signal.target)} (stop ${Math.round(s.signal.stop)})`;
  } else {
    elVerdict.className = "standby";
    elVerdict.textContent = `STANDBY — ${s.rejection_reason || "…"}`;
  }
  setText("quality", s.quality.toFixed(2));
  setText("oi", `${s.oi_delta_pct.toFixed(2)}%`);
  setText("oivel", s.oi_velocity.toFixed(3));
  setText("cvd", `${(s.cvd_30m / 1e6).toFixed(2)}M`);
  setText("funding", s.funding.toFixed(4));
  setText("imbalance", s.imbalance == null ? "—" : `${s.imbalance.toFixed(2)}x ${s.bigger_side}`);
  setText("taker", s.taker_ratio.toFixed(2));
  document.getElementById("c-liq").className = s.connections.liq_feed ? "" : "dim";
  document.getElementById("c-trade").className = s.connections.trade_feed ? "" : "dim";
}

// ---- render loop ----
function render() {
  if (!state.frozen) draw();
  requestAnimationFrame(render);
}

function draw() {
  const node = fieldSvg.node();
  const W = node.clientWidth, H = node.clientHeight;
  const now = Date.now();
  const x = d3.scaleLinear().domain([now - WINDOW_MS, now]).range([0, W]);

  // y centered on price; span includes nearest magnets
  let halfSpan = Math.max(state.price * 0.004, 50);
  const allMag = [...state.magnets.long, ...state.magnets.short].map((d) => d[0]);
  for (const p of allMag) halfSpan = Math.max(halfSpan, Math.abs(p - state.price) * 1.1);
  const y = makePriceScale(state.price, halfSpan, H);

  // magnet bands
  const bands = [
    ...state.magnets.long.map((d) => ({ price: d[0], usd: d[1], side: "long" })),
    ...state.magnets.short.map((d) => ({ price: d[0], usd: d[1], side: "short" })),
  ];
  const maxUsd = d3.max(bands, (b) => b.usd) || 1;
  const band = fieldSvg.selectAll("rect.magnet").data(bands, (b) => b.price);
  band.join(
    (enter) => enter.append("rect").attr("class", (b) => `magnet magnet-${b.side}`),
    (update) => update,
    (exit) => exit.remove()
  )
    .attr("x", 0).attr("width", W)
    .attr("y", (b) => y(b.price) - 6).attr("height", 12)
    .attr("opacity", (b) => 0.12 + 0.33 * (b.usd / maxUsd));

  // price line
  const trace = state.priceTrace.filter((d) => d.ts >= now - WINDOW_MS);
  const line = d3.line().x((d) => x(d.ts)).y((d) => y(d.price));
  let path = fieldSvg.selectAll("path.price").data([trace]);
  path = path.join("path").attr("class", "price")
    .attr("fill", "none").attr("stroke", "var(--price)").attr("stroke-width", 1.2)
    .attr("d", line);

  // current price line
  fieldSvg.selectAll("line.now").data([0]).join("line").attr("class", "now")
    .attr("x1", 0).attr("x2", W).attr("y1", y(state.price)).attr("y2", y(state.price))
    .attr("stroke", "var(--price)").attr("stroke-dasharray", "2 4").attr("opacity", 0.6);

  // blips
  state.blips = state.blips.filter((b) => b.ts * 1000 >= now - WINDOW_MS);
  const dots = fieldSvg.selectAll("circle.blip").data(state.blips, (b) => `${b.ts}-${b.price}`);
  dots.join(
    (enter) => enter.append("circle").attr("class", (b) => `blip dot-${b.side}`),
    (update) => update,
    (exit) => exit.remove()
  )
    .attr("cx", (b) => x(b.ts * 1000))
    .attr("cy", (b) => Math.max(2, Math.min(H - 2, y(b.price))))
    .attr("r", (b) => usdToRadius(b.usd))
    .attr("opacity", (b) => 0.25 + 0.6 * ageAlpha(now - b.ts * 1000));

  drawIntensity(now);
}

function drawIntensity(now) {
  const node = intSvg.node();
  const W = node.clientWidth, H = node.clientHeight;
  state.intensity = state.intensity.filter((d) => d.ts >= now - WINDOW_MS);
  const buckets = d3.rollup(
    state.intensity,
    (v) => d3.sum(v, (d) => d.side === "long" ? d.usd : -d.usd),
    (d) => Math.floor(d.ts / 5000)
  );
  const data = Array.from(buckets, ([k, net]) => ({ t: k * 5000, net }));
  const x = d3.scaleLinear().domain([now - WINDOW_MS, now]).range([0, W]);
  const maxAbs = d3.max(data, (d) => Math.abs(d.net)) || 1;
  const yh = d3.scaleLinear().domain([0, maxAbs]).range([0, H / 2]);
  const bars = intSvg.selectAll("rect.b").data(data, (d) => d.t);
  bars.join("rect").attr("class", "b")
    .attr("x", (d) => x(d.t)).attr("width", Math.max(1, W / 60))
    .attr("y", (d) => d.net >= 0 ? H / 2 - yh(Math.abs(d.net)) : H / 2)
    .attr("height", (d) => yh(Math.abs(d.net)))
    .attr("fill", (d) => d.net >= 0 ? "var(--long)" : "var(--short)");
}

document.addEventListener("keydown", (e) => {
  if (e.code === "Space") { state.frozen = !state.frozen; e.preventDefault(); }
});

connect();
render();
