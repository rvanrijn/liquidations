// Battle Trader Visualizer — Frontend

let priceChart, priceSeries, oiSeries, equityChart, equitySeries;
let markers = [];
let allCandles = [];

// --- Init ---

async function init() {
    buildSkipHoursGrid();
    bindSliders();

    // Create charts
    createPriceChart();
    createEquityChart();

    // Load data
    try {
        await loadCandles();
        await runSimulation();
    } catch (e) {
        console.error("Init error:", e);
    }

    document.getElementById("loading").classList.add("hidden");

    // Bind buttons
    document.getElementById("apply-btn").addEventListener("click", runSimulation);
    document.getElementById("sync-btn").addEventListener("click", syncVPS);
}

// --- Charts ---

function createPriceChart() {
    const container = document.getElementById("chart-container");
    priceChart = LightweightCharts.createChart(container, {
        layout: {
            background: { color: "#1a1a2e" },
            textColor: "#8892a0",
        },
        grid: {
            vertLines: { color: "#2a3550" },
            horzLines: { color: "#2a3550" },
        },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        rightPriceScale: { borderColor: "#2a3550" },
        timeScale: {
            borderColor: "#2a3550",
            timeVisible: true,
            secondsVisible: false,
        },
    });

    priceSeries = priceChart.addCandlestickSeries({
        upColor: "#00c853",
        downColor: "#ff1744",
        borderUpColor: "#00c853",
        borderDownColor: "#ff1744",
        wickUpColor: "#00c853",
        wickDownColor: "#ff1744",
    });

    oiSeries = priceChart.addLineSeries({
        color: "#448aff",
        lineWidth: 1,
        priceScaleId: "oi",
        title: "OI %",
    });

    priceChart.priceScale("oi").applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
    });

    // Resize observer
    const ro = new ResizeObserver(() => {
        priceChart.applyOptions({
            width: container.clientWidth,
            height: container.clientHeight,
        });
    });
    ro.observe(container);
}

function createEquityChart() {
    const container = document.getElementById("equity-container");
    equityChart = LightweightCharts.createChart(container, {
        layout: {
            background: { color: "#16213e" },
            textColor: "#8892a0",
        },
        grid: {
            vertLines: { visible: false },
            horzLines: { color: "#2a3550" },
        },
        rightPriceScale: { borderColor: "#2a3550" },
        timeScale: { visible: false },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    });

    equitySeries = equityChart.addAreaSeries({
        lineColor: "#7c4dff",
        topColor: "rgba(124, 77, 255, 0.3)",
        bottomColor: "rgba(124, 77, 255, 0.0)",
        lineWidth: 2,
    });

    const ro = new ResizeObserver(() => {
        equityChart.applyOptions({
            width: container.clientWidth,
            height: container.clientHeight,
        });
    });
    ro.observe(container);
}

// --- Data Loading ---

async function loadCandles() {
    const resp = await fetch("/api/candles");
    const data = await resp.json();
    allCandles = data.candles;
    if (allCandles.length > 0) {
        priceSeries.setData(allCandles);
    }
    if (data.oi.length > 0) {
        oiSeries.setData(data.oi);
    }
}

// --- Simulation ---

function getFilterParams() {
    const skipHours = [];
    document.querySelectorAll("#skip-hours input:checked").forEach(cb => {
        skipHours.push(parseInt(cb.value));
    });

    return {
        oi_gate_pct: parseInt(document.getElementById("oi-gate").value) / 100,
        cooldown_min: parseInt(document.getElementById("cooldown").value),
        skip_hours: skipHours,
        min_imbalance: parseInt(document.getElementById("min-imbalance").value) / 100,
        ha_min_streak: parseInt(document.getElementById("ha-streak").value),
        ha_min_body_ratio: parseInt(document.getElementById("ha-body").value) / 100,
        oi_deep_threshold: parseInt(document.getElementById("oi-deep").value) / 100,
    };
}

async function runSimulation() {
    const params = getFilterParams();
    const resp = await fetch("/api/simulate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(params),
    });
    const data = await resp.json();

    renderSummary(data.summary);
    renderTrades(data.trades);
    renderMarkers(data.trades);
    renderEquity(data.trades);
}

// --- Rendering ---

function renderSummary(s) {
    const pnlClass = s.total_pnl >= 0 ? "positive" : "negative";
    document.getElementById("summary").innerHTML = `
        <div class="stat-grid">
            <div class="stat-item">
                <span class="stat-label">Trades</span>
                <span class="stat-value">${s.trades_taken} / ${s.total_battles}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Win Rate</span>
                <span class="stat-value ${s.win_rate >= 55 ? 'positive' : s.win_rate < 45 ? 'negative' : ''}">${s.win_rate}%</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">PnL</span>
                <span class="stat-value ${pnlClass}">$${s.total_pnl.toFixed(0)}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Profit Factor</span>
                <span class="stat-value ${s.profit_factor >= 1.5 ? 'positive' : s.profit_factor < 1 ? 'negative' : ''}">${s.profit_factor}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Avg Win</span>
                <span class="stat-value positive">$${s.avg_win.toFixed(0)}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Avg Loss</span>
                <span class="stat-value negative">$${s.avg_loss.toFixed(0)}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Max DD</span>
                <span class="stat-value negative">${s.max_drawdown.toFixed(1)}%</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Balance</span>
                <span class="stat-value ${pnlClass}">$${s.final_balance.toFixed(0)}</span>
            </div>
        </div>
    `;
}

function renderTrades(trades) {
    const tbody = document.getElementById("trades-body");
    tbody.innerHTML = "";

    // Show most recent first
    const sorted = [...trades].reverse();

    for (const t of sorted) {
        const tr = document.createElement("tr");

        if (!t.passed) {
            tr.className = "filtered";
        } else if (t.pnl > 0) {
            tr.className = "win";
        } else if (t.pnl < 0) {
            tr.className = "loss";
        } else {
            tr.className = "be";
        }

        const filtersText = t.passed ? "" : t.filters_failed.join("; ");

        // move_pct and oi_change_pct are already in percentage form
        // e.g. 0.5097 means 0.51%, so display directly with .toFixed(2)
        const moveFmt = (t.move_pct >= 0 ? "+" : "") + t.move_pct.toFixed(2) + "%";
        const oiFmt = t.oi_change_pct.toFixed(2) + "%";

        tr.innerHTML = `
            <td>${t.entry_time_str}</td>
            <td>${t.direction[0]}</td>
            <td>$${t.entry_price.toFixed(0)}</td>
            <td>$${t.exit_price.toFixed(0)}</td>
            <td>${moveFmt}</td>
            <td>${t.passed ? '$' + (t.pnl >= 0 ? '+' : '') + t.pnl.toFixed(0) : '\u2014'}</td>
            <td>${oiFmt}</td>
            <td>${t.imbalance_ratio.toFixed(1)}x</td>
            <td>${t.exit_reason || '\u2014'}</td>
            <td class="filters-col" title="${filtersText}">${filtersText || '\u2713'}</td>
        `;

        // Click to scroll chart
        tr.addEventListener("click", () => {
            priceChart.timeScale().scrollToPosition(-findCandleIndex(t.timestamp), false);
        });

        tbody.appendChild(tr);
    }
}

function renderMarkers(trades) {
    const m = [];
    for (const t of trades) {
        if (!t.passed) continue;

        const candleTime = Math.floor(t.timestamp / 60) * 60;

        // Entry marker
        m.push({
            time: candleTime,
            position: t.direction === "LONG" ? "belowBar" : "aboveBar",
            color: t.pnl > 0 ? "#00c853" : t.pnl < 0 ? "#ff1744" : "#888",
            shape: t.direction === "LONG" ? "arrowUp" : "arrowDown",
            text: `${t.direction[0]} $${t.pnl >= 0 ? '+' : ''}${t.pnl.toFixed(0)}`,
        });
    }

    // Sort by time (required by lightweight-charts)
    m.sort((a, b) => a.time - b.time);
    priceSeries.setMarkers(m);
}

function renderEquity(trades) {
    const kept = trades.filter(t => t.passed);
    if (kept.length === 0) {
        equitySeries.setData([]);
        return;
    }

    const data = kept.map(t => ({
        time: Math.floor(t.timestamp / 60) * 60,
        value: t.balance_after,
    }));

    equitySeries.setData(data);
}

function findCandleIndex(timestamp) {
    const t = Math.floor(timestamp / 60) * 60;
    for (let i = allCandles.length - 1; i >= 0; i--) {
        if (allCandles[i].time <= t) {
            return allCandles.length - 1 - i;
        }
    }
    return 0;
}

// --- Skip Hours Grid ---

function buildSkipHoursGrid() {
    const container = document.getElementById("skip-hours");
    const defaults = new Set([7, 8, 15, 18]);
    for (let h = 0; h < 24; h++) {
        const label = document.createElement("label");
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.value = h;
        if (defaults.has(h)) cb.checked = true;
        const span = document.createElement("span");
        span.textContent = h.toString().padStart(2, "0");
        label.appendChild(cb);
        label.appendChild(span);
        container.appendChild(label);
    }
}

// --- Slider Bindings ---

function bindSliders() {
    const sliders = [
        { id: "oi-gate", valId: "oi-gate-val", fmt: v => (v / 100).toFixed(2) + "%" },
        { id: "cooldown", valId: "cooldown-val", fmt: v => v + "m" },
        { id: "min-imbalance", valId: "min-imbalance-val", fmt: v => (v / 100).toFixed(2) + "x" },
        { id: "ha-streak", valId: "ha-streak-val", fmt: v => v.toString() },
        { id: "ha-body", valId: "ha-body-val", fmt: v => (v / 100).toFixed(2) },
        { id: "oi-deep", valId: "oi-deep-val", fmt: v => (v / 100).toFixed(2) + "%" },
    ];

    for (const s of sliders) {
        const el = document.getElementById(s.id);
        const valEl = document.getElementById(s.valId);
        el.addEventListener("input", () => {
            valEl.textContent = s.fmt(parseInt(el.value));
        });
    }
}

// --- VPS Sync ---

async function syncVPS() {
    const btn = document.getElementById("sync-btn");
    btn.textContent = "Syncing...";
    btn.disabled = true;
    try {
        await fetch("/api/sync", { method: "POST" });
        await loadCandles();
        await runSimulation();
        btn.textContent = "Synced \u2713";
    } catch (e) {
        btn.textContent = "Sync Failed";
        console.error(e);
    }
    setTimeout(() => {
        btn.textContent = "Sync VPS";
        btn.disabled = false;
    }, 2000);
}

// --- Start ---

document.addEventListener("DOMContentLoaded", init);
