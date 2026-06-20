/* Liqhunt Cascade Radar — D3 frontend. */
"use strict";

// ---- pure helpers (console-testable) ----
const WINDOW_MS = 5 * 60 * 1000;       // 5 min visible
const MAX_BLIPS = 600;
const RIPPLE_USD = 250000;             // events above this drop a ripple ring
const RIPPLE_MS = 1600;                // ripple lifetime

function usdToRadius(usd) {            // log-scaled blip radius
  return Math.max(2, Math.min(30, 2 + Math.log10(Math.max(usd, 1)) * 3.2));
}
function ageAlpha(ageMs) {             // fade as event ages out of window
  return Math.max(0, 1 - ageMs / WINDOW_MS);
}
function makePriceScale(centerPrice, halfSpan, height) {
  return d3.scaleLinear()
    .domain([centerPrice - halfSpan, centerPrice + halfSpan])
    .range([height, 0]);
}
function fmtUsd(v) {                   // compact $ formatter
  const a = Math.abs(v);
  if (a >= 1e9) return (v / 1e9).toFixed(2) + "B";
  if (a >= 1e6) return (v / 1e6).toFixed(2) + "M";
  if (a >= 1e3) return (v / 1e3).toFixed(1) + "K";
  return v.toFixed(0);
}

// ---- state ----
const state = {
  price: 0, prevPrice: 0, magnets: { long: [], short: [] },
  signal: null, verdict: "none",
  blips: [],                 // {ts, side, usd, price}
  priceTrace: [],            // {ts, price}
  intensity: [],             // {ts, side, usd}
  frozen: false,
};

// ---- DOM ----
const fieldSvg = d3.select("#field");
const intSvg = d3.select("#intensity");
const qGauge = d3.select("#qgauge");
const elPrice = document.getElementById("price");
const elChg = document.getElementById("chg");
const elVerdict = document.getElementById("verdict");
const elClock = document.getElementById("clock");
const elDev = document.getElementById("devbadge");
const tooltip = document.getElementById("tooltip");

function setStat(id, v, cls) {
  const b = document.querySelector(`#${id} b`);
  b.textContent = v;
  b.className = cls || "";
}

// ---- one-time SVG defs (glow filters) ----
function installDefs(sel) {
  const defs = sel.append("defs");
  [["glow", 2.4], ["glowStrong", 4.2]].forEach(([id, dev]) => {
    const f = defs.append("filter").attr("id", id)
      .attr("x", "-80%").attr("y", "-80%").attr("width", "260%").attr("height", "260%");
    f.append("feGaussianBlur").attr("stdDeviation", dev).attr("result", "b");
    const m = f.append("feMerge");
    m.append("feMergeNode").attr("in", "b");
    m.append("feMergeNode").attr("in", "SourceGraphic");
  });
}
installDefs(fieldSvg);
installDefs(intSvg);
// persistent layer groups (z-order)
const gGrid = fieldSvg.append("g");
const gMagnet = fieldSvg.append("g");
const gPrice = fieldSvg.append("g");
const gRipple = fieldSvg.append("g");
const gBlip = fieldSvg.append("g").attr("filter", "url(#glow)");

// ---- quality radial gauge (static frame) ----
const QC = 66, QR = 52;
const qArc = d3.arc().innerRadius(QR - 9).outerRadius(QR).startAngle(-Math.PI * 0.75);
qGauge.append("path").attr("transform", `translate(${QC},${QC})`)
  .attr("d", qArc.endAngle(Math.PI * 0.75)()).attr("fill", "#16202e");
const qFill = qGauge.append("path").attr("transform", `translate(${QC},${QC})`)
  .attr("filter", "url(#glow)");
const qText = qGauge.append("text").attr("x", QC).attr("y", QC + 9)
  .attr("text-anchor", "middle").attr("class", "qval").text("—");
function drawQuality(q) {
  const a0 = -Math.PI * 0.75, a1 = a0 + Math.PI * 1.5 * Math.max(0, Math.min(1, q));
  const col = q >= 0.66 ? "#2ee6a6" : q >= 0.4 ? "#ffd166" : "#5aa9ff";
  qFill.attr("d", qArc.startAngle(a0).endAngle(a1)()).attr("fill", col);
  qText.text(q.toFixed(2)).attr("fill", col);
}
drawQuality(0);

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
  state.prevPrice = state.price || s.price;
  state.price = s.price;
  state.magnets = s.magnets;
  state.verdict = s.verdict;
  state.signal = s.signal;
  if (s.price > 0) state.priceTrace.push({ ts: Date.now(), price: s.price });

  elPrice.textContent = `BTC $${Math.round(s.price).toLocaleString()}`;
  const d = s.price - state.prevPrice;
  if (Math.abs(d) >= 0.5) {
    elChg.textContent = (d > 0 ? "▲ " : "▼ ") + Math.abs(d).toFixed(0);
    elChg.className = "chg " + (d > 0 ? "up" : "down");
  }

  if (s.verdict === "ARMED" && s.signal) {
    elVerdict.className = "armed";
    elVerdict.textContent =
      `ARMED ${s.signal.direction} → ${Math.round(s.signal.target)} · stop ${Math.round(s.signal.stop)}`;
  } else {
    elVerdict.className = "standby";
    elVerdict.textContent = `STANDBY · ${s.rejection_reason || "…"}`;
  }

  drawQuality(s.quality || 0);
  setStat("oi", `${s.oi_delta_pct.toFixed(2)}%`, s.oi_delta_pct < 0 ? "neg" : "pos");
  setStat("oivel", s.oi_velocity.toFixed(3), s.oi_velocity < 0 ? "neg" : "pos");
  setStat("cvd", `${(s.cvd_30m / 1e6).toFixed(2)}M`, s.cvd_30m < 0 ? "neg" : "pos");
  setStat("funding", s.funding.toFixed(4), s.funding < 0 ? "neg" : "pos");
  setStat("taker", s.taker_ratio.toFixed(2));

  // imbalance split bar (reconstruct shares from ratio + bigger side)
  const imbVal = document.getElementById("imb-val");
  if (s.imbalance == null) {
    imbVal.textContent = "—";
    document.getElementById("imb-long").style.width = "50%";
    document.getElementById("imb-short").style.width = "50%";
  } else {
    imbVal.textContent = `${s.imbalance.toFixed(2)}× ${s.bigger_side}`;
    const big = s.imbalance / (s.imbalance + 1) * 100;
    const longPct = s.bigger_side === "LONG" ? big : 100 - big;
    document.getElementById("imb-long").style.width = longPct + "%";
    document.getElementById("imb-short").style.width = (100 - longPct) + "%";
  }

  document.getElementById("c-liq").classList.toggle("ok", !!s.connections.liq_feed);
  document.getElementById("c-trade").classList.toggle("ok", !!s.connections.trade_feed);
  elDev.classList.toggle("hidden", !s.dev_mode);
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
  const allMag = [...state.magnets.long, ...state.magnets.short].map((m) => m[0]);
  for (const p of allMag) halfSpan = Math.max(halfSpan, Math.abs(p - state.price) * 1.12);
  const y = makePriceScale(state.price, halfSpan, H);

  // gridlines + price-axis labels
  const ticks = y.ticks(6);
  const gl = gGrid.selectAll("line.gridline").data(ticks);
  gl.join("line").attr("class", "gridline")
    .attr("x1", 0).attr("x2", W).attr("y1", (d) => y(d)).attr("y2", (d) => y(d));
  const lbl = gGrid.selectAll("text.axislabel").data(ticks);
  lbl.join("text").attr("class", "axislabel")
    .attr("x", W - 6).attr("y", (d) => y(d) - 4).attr("text-anchor", "end")
    .text((d) => d3.format(",")(Math.round(d)));

  // magnet bands + labels
  const bands = [
    ...state.magnets.long.map((m) => ({ price: m[0], usd: m[1], side: "long" })),
    ...state.magnets.short.map((m) => ({ price: m[0], usd: m[1], side: "short" })),
  ];
  const maxUsd = d3.max(bands, (b) => b.usd) || 1;
  gMagnet.selectAll("rect.magnet").data(bands, (b) => b.side + b.price).join(
    (en) => en.append("rect").attr("class", (b) => `magnet magnet-${b.side}`)
      .attr("filter", "url(#glow)"),
    (up) => up, (ex) => ex.remove()
  ).attr("x", 0).attr("width", W)
    .attr("y", (b) => y(b.price) - 7).attr("height", 14)
    .attr("opacity", (b) => 0.10 + 0.34 * (b.usd / maxUsd));
  gMagnet.selectAll("text.magnet-label").data(bands, (b) => b.side + b.price).join("text")
    .attr("class", (b) => `magnet-label magnet-${b.side}`)
    .attr("x", 8).attr("y", (b) => y(b.price) + 3)
    .attr("fill", (b) => b.side === "long" ? "var(--long)" : "var(--short)")
    .text((b) => `${b.side === "long" ? "▼" : "▲"} ${Math.round(b.price).toLocaleString()} · $${fmtUsd(b.usd)}`);

  // price trace + now marker
  const trace = state.priceTrace = state.priceTrace.filter((d) => d.ts >= now - WINDOW_MS);
  const line = d3.line().x((d) => x(d.ts)).y((d) => y(d.price)).curve(d3.curveMonotoneX);
  gPrice.selectAll("path.pricepath").data([trace]).join("path")
    .attr("class", "pricepath").attr("filter", "url(#glow)").attr("d", line);
  gPrice.selectAll("line.nowline").data([0]).join("line").attr("class", "nowline")
    .attr("x1", 0).attr("x2", W).attr("y1", y(state.price)).attr("y2", y(state.price));
  gPrice.selectAll("circle.nowdot").data([0]).join("circle").attr("class", "nowdot")
    .attr("filter", "url(#glowStrong)").attr("cx", W - 2).attr("cy", y(state.price)).attr("r", 3.5);

  // ripple rings for large recent liquidations
  const rips = state.blips.filter((b) => b.usd >= RIPPLE_USD && (now - b.ts * 1000) < RIPPLE_MS);
  gRipple.selectAll("circle.ring").data(rips, (b) => `${b.ts}-${b.price}`).join(
    (en) => en.append("circle").attr("class", (b) => `ring ring-${b.side}`),
    (up) => up, (ex) => ex.remove()
  ).attr("cx", (b) => x(b.ts * 1000))
    .attr("cy", (b) => Math.max(2, Math.min(H - 2, y(b.price))))
    .attr("r", (b) => { const t = (now - b.ts * 1000) / RIPPLE_MS; return usdToRadius(b.usd) + t * 46; })
    .attr("stroke-width", 1.5)
    .attr("opacity", (b) => 0.6 * (1 - (now - b.ts * 1000) / RIPPLE_MS));

  // blips
  state.blips = state.blips.filter((b) => b.ts * 1000 >= now - WINDOW_MS);
  gBlip.selectAll("circle.blip").data(state.blips, (b) => `${b.ts}-${b.price}`).join(
    (en) => en.append("circle").attr("class", (b) => `blip dot-${b.side}`)
      .on("mousemove", (ev, b) => showTip(ev, b)).on("mouseout", hideTip),
    (up) => up, (ex) => ex.remove()
  ).attr("cx", (b) => x(b.ts * 1000))
    .attr("cy", (b) => Math.max(2, Math.min(H - 2, y(b.price))))
    .attr("r", (b) => usdToRadius(b.usd))
    .attr("opacity", (b) => 0.30 + 0.6 * ageAlpha(now - b.ts * 1000));

  drawIntensity(now);
}

function drawIntensity(now) {
  const node = intSvg.node();
  const W = node.clientWidth, H = node.clientHeight;
  state.intensity = state.intensity.filter((d) => d.ts >= now - WINDOW_MS);
  const buckets = d3.rollup(
    state.intensity,
    (v) => d3.sum(v, (d) => (d.side === "long" ? d.usd : -d.usd)),
    (d) => Math.floor(d.ts / 5000)
  );
  const data = Array.from(buckets, ([k, net]) => ({ t: k * 5000, net }));
  const x = d3.scaleLinear().domain([now - WINDOW_MS, now]).range([0, W]);
  const maxAbs = d3.max(data, (d) => Math.abs(d.net)) || 1;
  const yh = d3.scaleLinear().domain([0, maxAbs]).range([0, H / 2 - 3]);
  intSvg.selectAll("line.mid").data([0]).join("line").attr("class", "mid")
    .attr("x1", 0).attr("x2", W).attr("y1", H / 2).attr("y2", H / 2)
    .attr("stroke", "var(--grid)");
  intSvg.selectAll("rect.b").data(data, (d) => d.t).join("rect").attr("class", "b")
    .attr("filter", "url(#glow)")
    .attr("x", (d) => x(d.t)).attr("width", Math.max(1.5, W / 60 - 1))
    .attr("y", (d) => (d.net >= 0 ? H / 2 - yh(Math.abs(d.net)) : H / 2))
    .attr("height", (d) => yh(Math.abs(d.net)))
    .attr("rx", 1)
    .attr("fill", (d) => (d.net >= 0 ? "var(--long)" : "var(--short)"));
}

// ---- tooltip ----
function showTip(ev, b) {
  tooltip.classList.remove("hidden");
  tooltip.style.left = ev.clientX + 12 + "px";
  tooltip.style.top = ev.clientY + 12 + "px";
  tooltip.innerHTML = `<b style="color:${b.side === "long" ? "var(--long)" : "var(--short)"}">`
    + `${b.side.toUpperCase()} liquidated</b><br>$${fmtUsd(b.usd)} @ ${Math.round(b.price).toLocaleString()}`;
}
function hideTip() { tooltip.classList.add("hidden"); }

// ---- clock ----
function tickClock() {
  const d = new Date();
  elClock.textContent = d.toLocaleTimeString([], { hour12: false });
}
setInterval(tickClock, 1000); tickClock();

document.addEventListener("keydown", (e) => {
  if (e.code === "Space") { state.frozen = !state.frozen; e.preventDefault(); }
});

connect();
render();
