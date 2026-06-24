# src/liqlevels/database.py
"""SQLite database for magnet battle logging."""

import sqlite3
from pathlib import Path
from typing import Any

from src.liqlevels.models import Battle


class MagnetDatabase:
    """SQLite wrapper for magnet battle logging and statistics."""

    def __init__(self, db_path: str = "data/magnet_battles.db"):
        db_file = Path(db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = str(db_file)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS battles_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bigger_side TEXT NOT NULL,
                moved_side TEXT NOT NULL,
                hypothesis_correct INTEGER NOT NULL,
                imbalance_ratio REAL NOT NULL,
                duration_seconds REAL NOT NULL,
                snapshot_price REAL NOT NULL,
                resolved_price REAL NOT NULL,
                move_pct REAL NOT NULL,
                total_long_usd REAL NOT NULL,
                total_short_usd REAL NOT NULL,
                timestamp REAL NOT NULL
            )
        """)
        # Migrate: add OI columns if missing
        for col in ("oi_start_usd", "oi_end_usd", "oi_change_pct"):
            try:
                self.conn.execute(
                    f"ALTER TABLE battles_v2 ADD COLUMN {col} REAL DEFAULT 0"
                )
            except sqlite3.OperationalError:
                pass  # column already exists
        self.conn.commit()

    def log_battle(self, battle: Battle) -> int:
        cursor = self.conn.execute("""
            INSERT INTO battles_v2 (
                bigger_side, moved_side, hypothesis_correct, imbalance_ratio,
                duration_seconds, snapshot_price, resolved_price, move_pct,
                total_long_usd, total_short_usd, timestamp,
                oi_start_usd, oi_end_usd, oi_change_pct
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            battle.bigger_side,
            battle.moved_side,
            int(battle.hypothesis_correct),
            battle.imbalance_ratio,
            battle.duration_seconds,
            battle.snapshot_price,
            battle.resolved_price,
            battle.move_pct,
            battle.total_long_usd,
            battle.total_short_usd,
            battle.timestamp,
            battle.oi_start_usd,
            battle.oi_end_usd,
            battle.oi_change_pct,
        ))
        self.conn.commit()
        return cursor.lastrowid

    def get_stats(self) -> dict[str, Any]:
        """Get all-time magnet hypothesis stats."""
        row = self.conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(hypothesis_correct) as correct,
                AVG(duration_seconds) as avg_duration
            FROM battles_v2
        """).fetchone()
        total = row["total"] or 0
        correct = row["correct"] or 0
        return {
            "total": total,
            "correct": correct,
            "incorrect": total - correct,
            "accuracy": (correct / total * 100) if total > 0 else 0.0,
            "avg_duration": row["avg_duration"] or 0.0,
        }

    def get_stats_by_imbalance(self, min_ratio: float) -> dict[str, Any]:
        """Get stats filtered by minimum imbalance ratio."""
        row = self.conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(hypothesis_correct) as correct,
                AVG(duration_seconds) as avg_duration
            FROM battles_v2
            WHERE imbalance_ratio >= ?
        """, (min_ratio,)).fetchone()
        total = row["total"] or 0
        correct = row["correct"] or 0
        return {
            "total": total,
            "correct": correct,
            "incorrect": total - correct,
            "accuracy": (correct / total * 100) if total > 0 else 0.0,
            "avg_duration": row["avg_duration"] or 0.0,
        }

    def get_recent_battles(self, limit: int = 8) -> list[Battle]:
        """Get most recent battles."""
        cursor = self.conn.execute("""
            SELECT * FROM battles_v2
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,))
        return [self._row_to_battle(row) for row in cursor.fetchall()]

    def _row_to_battle(self, row: sqlite3.Row) -> Battle:
        return Battle(
            id=row["id"],
            bigger_side=row["bigger_side"],
            moved_side=row["moved_side"],
            hypothesis_correct=bool(row["hypothesis_correct"]),
            imbalance_ratio=row["imbalance_ratio"],
            duration_seconds=row["duration_seconds"],
            snapshot_price=row["snapshot_price"],
            resolved_price=row["resolved_price"],
            move_pct=row["move_pct"],
            total_long_usd=row["total_long_usd"],
            total_short_usd=row["total_short_usd"],
            timestamp=row["timestamp"],
            oi_start_usd=row["oi_start_usd"] or 0.0,
            oi_end_usd=row["oi_end_usd"] or 0.0,
            oi_change_pct=row["oi_change_pct"] or 0.0,
        )

    def close(self) -> None:
        if self.conn:
            self.conn.close()
