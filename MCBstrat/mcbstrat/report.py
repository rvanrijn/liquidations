"""Render the backtest report as markdown."""

def render_markdown(p: dict) -> str:
    c = p["cost"]["all"]
    lines = [
        f"# MCB Cipher B 8H HA Backtest — {p['symbol']}",
        "",
        f"- **Span:** {p['span']}  ·  **Bars:** {p['bars']}",
        f"- **TV cross-check:** {p['tv_crosscheck']}",
        "",
        "## Headline (with costs)",
        f"- **Trade count:** {c['n']}  (Long {p['cost']['long']['n']} / Short {p['cost']['short']['n']})",
        f"- Win rate: {c['win_rate']:.1%}  ·  Profit factor: {c['profit_factor']:.2f}",
        f"- Expectancy/trade: {c['expectancy']:.2%}  ·  Total return: {c['total_return']:.1%}",
        f"- Max drawdown: {c['max_dd']:.1%}  ·  Avg bars held: {c['avg_bars']:.1f}",
        "",
        "## Zero-cost (raw signal edge)",
        f"- Total return: {p['zero_cost']['all']['total_return']:.1%}",
        "",
        "## Long vs Short",
        f"- Long verdict: **{p['verdict_long']}**",
        f"- Short verdict: **{p['verdict_short']}**",
        "",
        "## Out-of-sample (70/30)",
        f"- In-sample expectancy: {p['oos']['in']['expectancy']:.2%}",
        f"- Out-of-sample expectancy: {p['oos']['out']['expectancy']:.2%}",
        "",
        "## Regime breakdown",
    ]
    for name, r in p["regimes"].items():
        lines.append(f"- {name}: n={r['n']}, expectancy {r['expectancy']:.2%}")
    lines += ["", "## Verdict", p.get("final_verdict", "_fill in after reviewing the numbers_"), ""]
    return "\n".join(lines)
