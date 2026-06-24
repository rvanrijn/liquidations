# src/krakenbot/backtest.py
"""Battle strategy backtester — sweep entry/exit parameters against historical battles.

Phase 1: DB sweep (fast, in-memory, against stored battles)
Phase 2: Full replay (1m candles + OI, tick-by-tick) — TODO

Usage:
    uv run krakenbot-backtest --phase 1
    uv run krakenbot-backtest --phase 1 --db data/krakenbot.db --table battles
"""

import argparse
import sqlite3
from dataclasses import dataclass
from itertools import product
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.text import Text


@dataclass
class BattleRow:
    bigger_side: str
    moved_side: str
    hypothesis_correct: bool
    imbalance_ratio: float
    snapshot_price: float
    resolved_price: float
    move_pct: float
    oi_change_pct: float
    duration_seconds: float
    mfe_pct: float
    mae_pct: float


@dataclass
class SweepResult:
    resolve_pct: float
    oi_gate: float | None  # None = no gate
    min_imbalance: float
    entry_slip: float
    entry_mode: str  # "market" or "limit"
    trades: int
    wins: int
    losses: int
    win_rate: float
    total_pnl_pct: float
    total_pnl_usd: float
    profit_factor: float
    max_dd_pct: float
    avg_pnl_pct: float
    avg_win_pct: float
    avg_loss_pct: float


NOTIONAL = 2300  # approximate notional at 5x leverage on $460
TAKER_FEE_PCT = 0.05  # 0.05% round-trip taker fee
MAKER_FEE_PCT = 0.02  # 0.02% maker fee (limit orders)


def load_battles(db_path: str, table: str = "battles_v2") -> list[BattleRow]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(f"""
        SELECT bigger_side, moved_side, hypothesis_correct, imbalance_ratio,
               snapshot_price, resolved_price, move_pct, oi_change_pct,
               duration_seconds, mfe_pct, mae_pct
        FROM {table} ORDER BY timestamp
    """).fetchall()
    conn.close()
    return [
        BattleRow(
            bigger_side=r["bigger_side"],
            moved_side=r["moved_side"],
            hypothesis_correct=bool(r["hypothesis_correct"]),
            imbalance_ratio=r["imbalance_ratio"],
            snapshot_price=r["snapshot_price"],
            resolved_price=r["resolved_price"],
            move_pct=r["move_pct"],
            oi_change_pct=r["oi_change_pct"] or 0.0,
            duration_seconds=r["duration_seconds"],
            mfe_pct=r["mfe_pct"] or 0.0,
            mae_pct=r["mae_pct"] or 0.0,
        )
        for r in rows
    ]


def run_sweep(battles: list[BattleRow], resolve_pct: float, oi_gate: float | None,
              min_imbalance: float, entry_slip: float, entry_mode: str) -> SweepResult:
    """Simulate trades for one parameter combination."""
    wins = 0
    losses = 0
    gross_win = 0.0
    gross_loss = 0.0
    equity_curve = [0.0]  # cumulative P&L %
    fee_pct = MAKER_FEE_PCT if entry_mode == "limit" else TAKER_FEE_PCT
    win_pnls: list[float] = []
    loss_pnls: list[float] = []

    for b in battles:
        # Filter: imbalance threshold
        if b.imbalance_ratio < min_imbalance:
            continue

        # Filter: OI gate (None = no gate = always pass)
        if oi_gate is not None and b.oi_change_pct > oi_gate:
            continue

        # Filter: battle must have resolved far enough for this resolve_pct
        # The battle resolved at ±RESOLVE_MOVE_PCT (0.5%) from snapshot.
        # If we're testing a different resolve_pct, we need to check if the
        # battle's actual move was large enough.
        # For resolve_pct <= 0.5 (the recording threshold), all battles qualify.
        # For resolve_pct > 0.5, we need abs(move_pct) >= resolve_pct.
        if abs(b.move_pct) < resolve_pct:
            continue

        # Direction: SHORT if bigger=LONG, LONG if bigger=SHORT
        direction = "SHORT" if b.bigger_side == "LONG" else "LONG"

        # Entry price: snapshot + slippage (slippage makes entry worse)
        if direction == "SHORT":
            entry_price = b.snapshot_price * (1 - entry_slip / 100)  # enter lower = worse for short
        else:
            entry_price = b.snapshot_price * (1 + entry_slip / 100)  # enter higher = worse for long

        # Exit price: for resolve_pct <= 0.5, use resolved_price.
        # For resolve_pct > 0.5, the exit would be at snapshot ± resolve_pct.
        if resolve_pct <= 0.5:
            exit_price = b.resolved_price
        else:
            # Simulate: if battle direction matches hypothesis, price moved resolve_pct in expected direction
            if b.hypothesis_correct:
                if b.bigger_side == "LONG":
                    exit_price = b.snapshot_price * (1 - resolve_pct / 100)
                else:
                    exit_price = b.snapshot_price * (1 + resolve_pct / 100)
            else:
                if b.bigger_side == "LONG":
                    exit_price = b.snapshot_price * (1 + resolve_pct / 100)
                else:
                    exit_price = b.snapshot_price * (1 - resolve_pct / 100)

        # P&L calculation
        if direction == "SHORT":
            pnl_pct = (entry_price - exit_price) / entry_price * 100
        else:
            pnl_pct = (exit_price - entry_price) / entry_price * 100

        # Subtract fees
        pnl_pct -= fee_pct

        if pnl_pct > 0:
            wins += 1
            gross_win += pnl_pct
            win_pnls.append(pnl_pct)
        else:
            losses += 1
            gross_loss += abs(pnl_pct)
            loss_pnls.append(pnl_pct)

        equity_curve.append(equity_curve[-1] + pnl_pct)

    total = wins + losses
    if total == 0:
        return SweepResult(
            resolve_pct=resolve_pct, oi_gate=oi_gate, min_imbalance=min_imbalance,
            entry_slip=entry_slip, entry_mode=entry_mode,
            trades=0, wins=0, losses=0, win_rate=0, total_pnl_pct=0,
            total_pnl_usd=0, profit_factor=0, max_dd_pct=0, avg_pnl_pct=0,
            avg_win_pct=0, avg_loss_pct=0,
        )

    total_pnl_pct = equity_curve[-1]
    peak = equity_curve[0]
    max_dd = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > max_dd:
            max_dd = dd

    return SweepResult(
        resolve_pct=resolve_pct,
        oi_gate=oi_gate,
        min_imbalance=min_imbalance,
        entry_slip=entry_slip,
        entry_mode=entry_mode,
        trades=total,
        wins=wins,
        losses=losses,
        win_rate=wins / total * 100,
        total_pnl_pct=total_pnl_pct,
        total_pnl_usd=total_pnl_pct / 100 * NOTIONAL,
        profit_factor=gross_win / gross_loss if gross_loss > 0 else float("inf"),
        max_dd_pct=max_dd,
        avg_pnl_pct=total_pnl_pct / total,
        avg_win_pct=sum(win_pnls) / len(win_pnls) if win_pnls else 0,
        avg_loss_pct=sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0,
    )


def phase1(db_path: str, table: str, top_n: int = 25) -> None:
    """Run Phase 1: DB battle sweep."""
    console = Console()
    console.print(f"\n[bold]Phase 1: Battle Parameter Sweep[/bold]")
    console.print(f"DB: {db_path} | Table: {table}\n")

    battles = load_battles(db_path, table)
    console.print(f"Loaded {len(battles)} battles")

    # Count battles with OI data
    has_oi = sum(1 for b in battles if b.oi_change_pct != 0)
    console.print(f"Battles with OI data: {has_oi}/{len(battles)}")
    console.print()

    # Parameter grid
    resolve_pcts = [0.3, 0.4, 0.5, 0.6, 0.75, 1.0]
    oi_gates: list[float | None] = [None, -0.1, -0.2, -0.3, -0.5]
    min_imbalances = [1.1, 1.25, 1.5, 1.75, 2.0]
    entry_slips = [0.0, 0.05, 0.10, 0.15, 0.20, 0.25]
    entry_modes = ["market"]  # limit tested separately

    combos = list(product(resolve_pcts, oi_gates, min_imbalances, entry_slips, entry_modes))
    console.print(f"Sweeping {len(combos)} combinations...")

    results: list[SweepResult] = []
    for resolve, oi, imb, slip, mode in combos:
        r = run_sweep(battles, resolve, oi, imb, slip, mode)
        if r.trades > 0:
            results.append(r)

    # Also test limit orders (0% slip, maker fee)
    for resolve, oi, imb in product(resolve_pcts, oi_gates, min_imbalances):
        r = run_sweep(battles, resolve, oi, imb, 0.0, "limit")
        if r.trades > 0:
            results.append(r)

    # Sort by total P&L
    results.sort(key=lambda r: r.total_pnl_usd, reverse=True)

    # Top N results table
    t = Table(title=f"TOP {top_n} CONFIGS BY NET P&L", expand=True)
    t.add_column("#", width=3, justify="right", style="dim")
    t.add_column("Resolve", width=7, justify="right")
    t.add_column("OI Gate", width=7, justify="right")
    t.add_column("Imb", width=5, justify="right")
    t.add_column("Slip", width=5, justify="right")
    t.add_column("Mode", width=6)
    t.add_column("Trades", width=6, justify="right")
    t.add_column("WR", width=5, justify="right")
    t.add_column("PF", width=5, justify="right")
    t.add_column("P&L%", width=7, justify="right")
    t.add_column("P&L$", width=8, justify="right")
    t.add_column("MaxDD", width=6, justify="right")
    t.add_column("AvgW", width=6, justify="right")
    t.add_column("AvgL", width=6, justify="right")

    for i, r in enumerate(results[:top_n], 1):
        pnl_style = "green" if r.total_pnl_usd > 0 else "red"
        oi_str = f"{r.oi_gate:.1f}%" if r.oi_gate is not None else "none"
        t.add_row(
            str(i),
            f"{r.resolve_pct:.1f}%",
            oi_str,
            f"{r.min_imbalance:.2f}",
            f"{r.entry_slip:.2f}%",
            r.entry_mode,
            str(r.trades),
            f"{r.win_rate:.0f}%",
            f"{r.profit_factor:.2f}",
            Text(f"{r.total_pnl_pct:+.1f}%", style=pnl_style),
            Text(f"${r.total_pnl_usd:+,.0f}", style=pnl_style),
            f"{r.max_dd_pct:.1f}%",
            f"{r.avg_win_pct:.2f}%",
            f"{r.avg_loss_pct:.2f}%",
        )
    console.print(t)

    # Bottom 10 (worst configs)
    console.print()
    tw = Table(title="BOTTOM 10 CONFIGS (WORST)", expand=True)
    tw.add_column("#", width=3, justify="right", style="dim")
    tw.add_column("Resolve", width=7, justify="right")
    tw.add_column("OI Gate", width=7, justify="right")
    tw.add_column("Imb", width=5, justify="right")
    tw.add_column("Slip", width=5, justify="right")
    tw.add_column("Mode", width=6)
    tw.add_column("Trades", width=6, justify="right")
    tw.add_column("WR", width=5, justify="right")
    tw.add_column("P&L$", width=8, justify="right")

    for i, r in enumerate(results[-10:], 1):
        pnl_style = "green" if r.total_pnl_usd > 0 else "red"
        oi_str = f"{r.oi_gate:.1f}%" if r.oi_gate is not None else "none"
        tw.add_row(
            str(i),
            f"{r.resolve_pct:.1f}%",
            oi_str,
            f"{r.min_imbalance:.2f}",
            f"{r.entry_slip:.2f}%",
            r.entry_mode,
            str(r.trades),
            f"{r.win_rate:.0f}%",
            Text(f"${r.total_pnl_usd:+,.0f}", style=pnl_style),
        )
    console.print(tw)

    # Parameter impact analysis
    console.print()
    _print_parameter_impact(console, results)

    # Current live config comparison
    console.print()
    _print_current_config(console, results)


def _print_parameter_impact(console: Console, results: list[SweepResult]) -> None:
    """Show how much each parameter affects P&L on average."""
    t = Table(title="PARAMETER IMPACT (avg P&L$ by parameter value)", expand=True)
    t.add_column("Parameter", width=12)
    t.add_column("Value", width=10, justify="right")
    t.add_column("Avg P&L$", width=10, justify="right")
    t.add_column("Avg Trades", width=10, justify="right")
    t.add_column("Avg WR", width=8, justify="right")

    # Resolve %
    for val in sorted(set(r.resolve_pct for r in results)):
        subset = [r for r in results if r.resolve_pct == val]
        avg_pnl = sum(r.total_pnl_usd for r in subset) / len(subset)
        avg_trades = sum(r.trades for r in subset) / len(subset)
        avg_wr = sum(r.win_rate for r in subset) / len(subset)
        style = "green" if avg_pnl > 0 else "red"
        t.add_row("Resolve", f"{val:.1f}%",
                   Text(f"${avg_pnl:+,.0f}", style=style),
                   f"{avg_trades:.0f}", f"{avg_wr:.0f}%")

    t.add_section()

    # OI gate
    for val in [None, -0.1, -0.2, -0.3, -0.5]:
        subset = [r for r in results if r.oi_gate == val]
        if not subset:
            continue
        avg_pnl = sum(r.total_pnl_usd for r in subset) / len(subset)
        avg_trades = sum(r.trades for r in subset) / len(subset)
        avg_wr = sum(r.win_rate for r in subset) / len(subset)
        style = "green" if avg_pnl > 0 else "red"
        val_str = "none" if val is None else f"{val:.1f}%"
        t.add_row("OI Gate", val_str,
                   Text(f"${avg_pnl:+,.0f}", style=style),
                   f"{avg_trades:.0f}", f"{avg_wr:.0f}%")

    t.add_section()

    # Min imbalance
    for val in sorted(set(r.min_imbalance for r in results)):
        subset = [r for r in results if r.min_imbalance == val]
        avg_pnl = sum(r.total_pnl_usd for r in subset) / len(subset)
        avg_trades = sum(r.trades for r in subset) / len(subset)
        avg_wr = sum(r.win_rate for r in subset) / len(subset)
        style = "green" if avg_pnl > 0 else "red"
        t.add_row("Imbalance", f"{val:.2f}x",
                   Text(f"${avg_pnl:+,.0f}", style=style),
                   f"{avg_trades:.0f}", f"{avg_wr:.0f}%")

    t.add_section()

    # Entry slippage
    for val in sorted(set(r.entry_slip for r in results)):
        subset = [r for r in results if r.entry_slip == val]
        avg_pnl = sum(r.total_pnl_usd for r in subset) / len(subset)
        avg_trades = sum(r.trades for r in subset) / len(subset)
        avg_wr = sum(r.win_rate for r in subset) / len(subset)
        style = "green" if avg_pnl > 0 else "red"
        t.add_row("Slippage", f"{val:.2f}%",
                   Text(f"${avg_pnl:+,.0f}", style=style),
                   f"{avg_trades:.0f}", f"{avg_wr:.0f}%")

    t.add_section()

    # Entry mode
    for val in sorted(set(r.entry_mode for r in results)):
        subset = [r for r in results if r.entry_mode == val]
        avg_pnl = sum(r.total_pnl_usd for r in subset) / len(subset)
        avg_trades = sum(r.trades for r in subset) / len(subset)
        avg_wr = sum(r.win_rate for r in subset) / len(subset)
        style = "green" if avg_pnl > 0 else "red"
        t.add_row("Mode", val,
                   Text(f"${avg_pnl:+,.0f}", style=style),
                   f"{avg_trades:.0f}", f"{avg_wr:.0f}%")

    console.print(t)


def _print_current_config(console: Console, results: list[SweepResult]) -> None:
    """Show the result for the current live config."""
    # Current live: resolve=0.5%, oi_gate=-0.3%, imbalance=1.25, slip=0.15% (estimated), market
    current = [r for r in results
               if r.resolve_pct == 0.5 and r.oi_gate == -0.3
               and r.min_imbalance == 1.25 and r.entry_slip == 0.15
               and r.entry_mode == "market"]

    # Also show with limit orders
    current_limit = [r for r in results
                     if r.resolve_pct == 0.5 and r.oi_gate == -0.3
                     and r.min_imbalance == 1.25 and r.entry_slip == 0.0
                     and r.entry_mode == "limit"]

    t = Table(title="CURRENT LIVE CONFIG vs LIMIT", expand=True)
    t.add_column("Config", width=20)
    t.add_column("Trades", width=7, justify="right")
    t.add_column("WR", width=6, justify="right")
    t.add_column("PF", width=6, justify="right")
    t.add_column("P&L$", width=10, justify="right")
    t.add_column("MaxDD", width=7, justify="right")
    t.add_column("Rank", width=6, justify="right")

    for label, subset in [("Market (0.15% slip)", current), ("Limit (0% slip)", current_limit)]:
        if subset:
            r = subset[0]
            rank = sorted(results, key=lambda x: x.total_pnl_usd, reverse=True).index(r) + 1
            pnl_style = "green" if r.total_pnl_usd > 0 else "red"
            t.add_row(
                label,
                str(r.trades),
                f"{r.win_rate:.0f}%",
                f"{r.profit_factor:.2f}",
                Text(f"${r.total_pnl_usd:+,.0f}", style=pnl_style),
                f"{r.max_dd_pct:.1f}%",
                f"#{rank}/{len(results)}",
            )

    console.print(t)


def main():
    parser = argparse.ArgumentParser(description="Battle strategy backtester")
    parser.add_argument("--phase", type=int, default=1, choices=[1, 2])
    parser.add_argument("--db", default="data/magnet_battles.db", help="Path to battles DB")
    parser.add_argument("--table", default="battles_v2", help="Table name (battles_v2 or battles)")
    parser.add_argument("--top", type=int, default=25, help="Show top N results")
    args = parser.parse_args()

    if args.phase == 1:
        phase1(args.db, args.table, args.top)
    else:
        print("Phase 2 not yet implemented — use --phase 1")


if __name__ == "__main__":
    main()
