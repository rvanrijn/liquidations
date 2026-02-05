# src/scalper/database.py
"""SQLite database wrapper for scalping bot trade logging."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.scalper.models import TradeRecord


class TradeDatabase:
    """SQLite wrapper for trade logging and statistics."""

    def __init__(self, db_path: str = "data/scalper_trades.db"):
        """Initialize database connection and create tables if needed.

        Args:
            db_path: Path to SQLite database file (relative to project root)
        """
        # Ensure data directory exists
        db_file = Path(db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)

        self.db_path = str(db_file)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self) -> None:
        """Create trades table if it doesn't exist."""
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                side TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                size_usd REAL NOT NULL,
                leverage INTEGER NOT NULL,
                pnl_usd REAL NOT NULL,
                pnl_percent REAL NOT NULL,
                r_multiple REAL NOT NULL,
                entry_time INTEGER NOT NULL,
                exit_time INTEGER NOT NULL,
                duration_s INTEGER NOT NULL,
                exit_reason TEXT NOT NULL,
                bias TEXT NOT NULL,
                delta_5m REAL NOT NULL,
                delta_15m REAL NOT NULL,
                buy_pressure REAL NOT NULL,
                vwap REAL NOT NULL,
                long_liq_usd REAL NOT NULL,
                short_liq_usd REAL NOT NULL
            )
        """)
        self.conn.commit()

    def log_trade(self, record: TradeRecord) -> int:
        """Insert a trade record into the database.

        Args:
            record: TradeRecord to insert

        Returns:
            The auto-generated trade ID
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO trades (
                side, entry_price, exit_price, size_usd, leverage,
                pnl_usd, pnl_percent, r_multiple, entry_time, exit_time,
                duration_s, exit_reason, bias, delta_5m, delta_15m,
                buy_pressure, vwap, long_liq_usd, short_liq_usd
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record.side,
            record.entry_price,
            record.exit_price,
            record.size_usd,
            record.leverage,
            record.pnl_usd,
            record.pnl_percent,
            record.r_multiple,
            record.entry_time,
            record.exit_time,
            record.duration_seconds,
            record.exit_reason,
            record.bias,
            record.delta_5m,
            record.delta_15m,
            record.buy_pressure,
            record.vwap,
            record.long_liq_usd,
            record.short_liq_usd,
        ))
        self.conn.commit()
        return cursor.lastrowid

    def get_today_trades(self) -> list[TradeRecord]:
        """Get all trades from today (UTC).

        Returns:
            List of TradeRecord objects from today
        """
        # Get start of today in UTC milliseconds
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        today_ms = int(today.timestamp() * 1000)

        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM trades
            WHERE entry_time >= ?
            ORDER BY entry_time DESC
        """, (today_ms,))

        return [self._row_to_trade_record(row) for row in cursor.fetchall()]

    def get_today_stats(self) -> dict[str, Any]:
        """Get statistics for today's trades (UTC).

        Returns:
            Dictionary with total_trades, wins, losses, win_rate, total_pnl, avg_r
        """
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        today_ms = int(today.timestamp() * 1000)

        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl_usd <= 0 THEN 1 ELSE 0 END) as losses,
                SUM(pnl_usd) as total_pnl,
                AVG(r_multiple) as avg_r
            FROM trades
            WHERE entry_time >= ?
        """, (today_ms,))

        row = cursor.fetchone()
        total_trades = row["total_trades"] or 0
        wins = row["wins"] or 0
        losses = row["losses"] or 0

        return {
            "total_trades": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate": (wins / total_trades * 100) if total_trades > 0 else 0.0,
            "total_pnl": row["total_pnl"] or 0.0,
            "avg_r": row["avg_r"] or 0.0,
        }

    def get_all_stats(self) -> dict[str, Any]:
        """Get all-time statistics.

        Returns:
            Dictionary with total_trades, wins, losses, win_rate, total_pnl, avg_r
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl_usd <= 0 THEN 1 ELSE 0 END) as losses,
                SUM(pnl_usd) as total_pnl,
                AVG(r_multiple) as avg_r
            FROM trades
        """)

        row = cursor.fetchone()
        total_trades = row["total_trades"] or 0
        wins = row["wins"] or 0
        losses = row["losses"] or 0

        return {
            "total_trades": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate": (wins / total_trades * 100) if total_trades > 0 else 0.0,
            "total_pnl": row["total_pnl"] or 0.0,
            "avg_r": row["avg_r"] or 0.0,
        }

    def close(self) -> None:
        """Close the database connection."""
        if self.conn:
            self.conn.close()

    def _row_to_trade_record(self, row: sqlite3.Row) -> TradeRecord:
        """Convert a database row to a TradeRecord object.

        Args:
            row: SQLite row object

        Returns:
            TradeRecord object
        """
        return TradeRecord(
            id=row["id"],
            side=row["side"],
            entry_price=row["entry_price"],
            exit_price=row["exit_price"],
            size_usd=row["size_usd"],
            leverage=row["leverage"],
            pnl_usd=row["pnl_usd"],
            pnl_percent=row["pnl_percent"],
            r_multiple=row["r_multiple"],
            entry_time=row["entry_time"],
            exit_time=row["exit_time"],
            duration_seconds=row["duration_s"],
            exit_reason=row["exit_reason"],
            bias=row["bias"],
            delta_5m=row["delta_5m"],
            delta_15m=row["delta_15m"],
            buy_pressure=row["buy_pressure"],
            vwap=row["vwap"],
            long_liq_usd=row["long_liq_usd"],
            short_liq_usd=row["short_liq_usd"],
        )

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - close connection."""
        self.close()
