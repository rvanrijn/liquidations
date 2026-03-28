# src/krakenbot/database.py
"""SQLite database for the Kraken live trading bot — fully independent from magnet_battles.db."""

import sqlite3
from pathlib import Path
from typing import Any

from src.krakenbot.models import LivePosition, LiveTrade


class KrakenBotDatabase:
    """SQLite wrapper for live position persistence, trade history, and order logging."""

    def __init__(self, db_path: str = "data/krakenbot.db"):
        db_file = Path(db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = str(db_file)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS live_position (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                entry_time REAL NOT NULL,
                size_contracts REAL NOT NULL,
                notional_usd REAL NOT NULL,
                stop_price REAL DEFAULT 0,
                snapshot_price REAL DEFAULT 0,
                kraken_order_id TEXT DEFAULT '',
                cli_ord_id TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS live_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                entry_time REAL NOT NULL,
                exit_time REAL NOT NULL,
                size_contracts REAL NOT NULL,
                notional_usd REAL NOT NULL,
                pnl_usd REAL NOT NULL,
                fee_usd REAL DEFAULT 0,
                balance_after REAL NOT NULL,
                move_pct REAL NOT NULL,
                exit_reason TEXT NOT NULL,
                entry_order_id TEXT DEFAULT '',
                exit_order_id TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS order_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT NOT NULL,
                size REAL NOT NULL,
                price REAL DEFAULT 0,
                cli_ord_id TEXT DEFAULT '',
                kraken_order_id TEXT DEFAULT '',
                status TEXT NOT NULL,
                error TEXT DEFAULT '',
                dry_run INTEGER NOT NULL
            );
        """)
        # Migrate: add columns if missing
        for table, col, default in [
            ("live_position", "oi_usd_at_entry", "0"),
            ("live_position", "taker_ratio", "1.0"),
            ("live_position", "cvd_30m", "0"),
            ("live_trades", "taker_ratio", "1.0"),
            ("live_trades", "cvd_30m", "0"),
            ("live_position", "mfe_pct", "0"),
            ("live_position", "mae_pct", "0"),
            ("live_trades", "mfe_pct", "0"),
            ("live_trades", "mae_pct", "0"),
        ]:
            try:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} REAL DEFAULT {default}")
            except sqlite3.OperationalError:
                pass  # column already exists
        self.conn.commit()


    # --- Live position persistence ---

    def save_live_position(self, pos: LivePosition) -> None:
        self.conn.execute("DELETE FROM live_position")
        self.conn.execute("""
            INSERT INTO live_position (id, direction, entry_price, entry_time,
                size_contracts, notional_usd, stop_price, snapshot_price,
                kraken_order_id, cli_ord_id, oi_usd_at_entry, taker_ratio, cvd_30m,
                mfe_pct, mae_pct)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            pos.direction, pos.entry_price, pos.entry_time,
            pos.size_contracts, pos.notional_usd, pos.stop_price,
            pos.snapshot_price, pos.kraken_order_id, pos.cli_ord_id,
            getattr(pos, 'oi_usd_at_entry', 0.0),
            getattr(pos, 'taker_ratio', 1.0),
            getattr(pos, 'cvd_30m', 0.0),
            pos.mfe_pct, pos.mae_pct,
        ))
        self.conn.commit()

    def load_live_position(self) -> LivePosition | None:
        row = self.conn.execute("SELECT * FROM live_position WHERE id = 1").fetchone()
        if row is None:
            return None
        oi_usd_at_entry = 0.0
        try:
            oi_usd_at_entry = row["oi_usd_at_entry"] or 0.0
        except (IndexError, KeyError):
            pass
        taker_ratio = 1.0
        cvd_30m = 0.0
        try:
            taker_ratio = row["taker_ratio"] or 1.0
        except (IndexError, KeyError):
            pass
        try:
            cvd_30m = row["cvd_30m"] or 0.0
        except (IndexError, KeyError):
            pass
        mfe_pct = 0.0
        mae_pct = 0.0
        try:
            mfe_pct = row["mfe_pct"] or 0.0
        except (IndexError, KeyError):
            pass
        try:
            mae_pct = row["mae_pct"] or 0.0
        except (IndexError, KeyError):
            pass
        return LivePosition(
            direction=row["direction"],
            entry_price=row["entry_price"],
            entry_time=row["entry_time"],
            size_contracts=row["size_contracts"],
            notional_usd=row["notional_usd"],
            stop_price=row["stop_price"] or 0.0,
            snapshot_price=row["snapshot_price"] or 0.0,
            kraken_order_id=row["kraken_order_id"] or "",
            cli_ord_id=row["cli_ord_id"] or "",
            oi_usd_at_entry=oi_usd_at_entry,
            taker_ratio=taker_ratio,
            cvd_30m=cvd_30m,
            mfe_pct=mfe_pct,
            mae_pct=mae_pct,
        )

    def clear_live_position(self) -> None:
        self.conn.execute("DELETE FROM live_position")
        self.conn.commit()

    # --- Live trades ---

    def log_live_trade(self, trade: LiveTrade) -> int:
        cursor = self.conn.execute("""
            INSERT INTO live_trades (
                direction, entry_price, exit_price, entry_time, exit_time,
                size_contracts, notional_usd, pnl_usd, fee_usd, balance_after,
                move_pct, exit_reason, entry_order_id, exit_order_id,
                taker_ratio, cvd_30m, mfe_pct, mae_pct
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade.direction, trade.entry_price, trade.exit_price,
            trade.entry_time, trade.exit_time, trade.size_contracts,
            trade.notional_usd, trade.pnl_usd, trade.fee_usd,
            trade.balance_after, trade.move_pct, trade.exit_reason,
            trade.entry_order_id, trade.exit_order_id,
            getattr(trade, 'taker_ratio', 1.0),
            getattr(trade, 'cvd_30m', 0.0),
            trade.mfe_pct, trade.mae_pct,
        ))
        self.conn.commit()
        return cursor.lastrowid

    def get_live_balance(self) -> float | None:
        row = self.conn.execute(
            "SELECT balance_after FROM live_trades ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row["balance_after"] if row else None

    def get_recent_live_trades(self, limit: int = 5) -> list[LiveTrade]:
        cursor = self.conn.execute(
            "SELECT * FROM live_trades ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [
            LiveTrade(
                id=row["id"],
                direction=row["direction"],
                entry_price=row["entry_price"],
                exit_price=row["exit_price"],
                entry_time=row["entry_time"],
                exit_time=row["exit_time"],
                size_contracts=row["size_contracts"],
                notional_usd=row["notional_usd"],
                pnl_usd=row["pnl_usd"],
                fee_usd=row["fee_usd"] or 0.0,
                balance_after=row["balance_after"],
                move_pct=row["move_pct"],
                exit_reason=row["exit_reason"],
                entry_order_id=row["entry_order_id"] or "",
                exit_order_id=row["exit_order_id"] or "",
                taker_ratio=row["taker_ratio"] if "taker_ratio" in row.keys() else 1.0,
                cvd_30m=row["cvd_30m"] if "cvd_30m" in row.keys() else 0.0,
                mfe_pct=row["mfe_pct"] if "mfe_pct" in row.keys() else 0.0,
                mae_pct=row["mae_pct"] if "mae_pct" in row.keys() else 0.0,
            )
            for row in cursor.fetchall()
        ]

    def get_live_stats(self) -> dict[str, Any]:
        row = self.conn.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
                   SUM(pnl_usd) as total_pnl,
                   AVG(pnl_usd) as avg_pnl,
                   MAX(balance_after) as peak_balance,
                   MIN(balance_after) as trough_balance
            FROM live_trades
        """).fetchone()
        total = row["total"] or 0
        wins = row["wins"] or 0
        return {
            "total": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": (wins / total * 100) if total > 0 else 0.0,
            "total_pnl": row["total_pnl"] or 0.0,
            "avg_pnl": row["avg_pnl"] or 0.0,
            "peak_balance": row["peak_balance"] or 0.0,
            "trough_balance": row["trough_balance"] or 0.0,
        }

    def get_daily_trades_count(self) -> int:
        """Count trades opened today (UTC)."""
        import time
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM live_trades WHERE entry_time >= ?",
            (start_of_day,),
        ).fetchone()
        return row["cnt"] or 0

    def get_daily_pnl(self) -> float:
        """Sum of P&L for trades closed today (UTC)."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        row = self.conn.execute(
            "SELECT COALESCE(SUM(pnl_usd), 0) as pnl FROM live_trades WHERE exit_time >= ?",
            (start_of_day,),
        ).fetchone()
        return row["pnl"]

    # --- Order logging ---

    def log_order(self, timestamp: float, side: str, order_type: str, size: float,
                  price: float, cli_ord_id: str, kraken_order_id: str,
                  status: str, error: str, dry_run: bool) -> int:
        cursor = self.conn.execute("""
            INSERT INTO order_log (timestamp, side, order_type, size, price,
                cli_ord_id, kraken_order_id, status, error, dry_run)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            timestamp, side, order_type, size, price,
            cli_ord_id, kraken_order_id, status, error, int(dry_run),
        ))
        self.conn.commit()
        return cursor.lastrowid

    def close(self) -> None:
        if self.conn:
            self.conn.close()
