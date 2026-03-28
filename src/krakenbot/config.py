# src/krakenbot/config.py
"""Bot configuration — all settings from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass
class BotConfig:
    """All bot settings, loaded from env vars with safe defaults."""

    # Kraken credentials
    kraken_api_key: str = field(default_factory=lambda: os.getenv("KRAKEN_FUTURES_API_KEY", ""))
    kraken_api_secret: str = field(default_factory=lambda: os.getenv("KRAKEN_FUTURES_API_SECRET", ""))

    # Mode flags
    demo: bool = field(default_factory=lambda: os.getenv("KRAKEN_DEMO", "true").lower() == "true")
    dry_run: bool = field(default_factory=lambda: os.getenv("BOT_DRY_RUN", "true").lower() == "true")

    # Trading parameters
    symbol: str = field(default_factory=lambda: os.getenv("KRAKEN_SYMBOL", "PF_XBTUSD"))
    leverage: int = field(default_factory=lambda: int(os.getenv("BOT_LEVERAGE", "3")))

    # Webhook
    webhook_secret: str = field(default_factory=lambda: os.getenv("WEBHOOK_SECRET", ""))
    webhook_port: int = field(default_factory=lambda: int(os.getenv("WEBHOOK_PORT", "8080")))
    webhook_host: str = field(default_factory=lambda: os.getenv("WEBHOOK_HOST", "0.0.0.0"))

    # Paths
    db_path: str = field(default_factory=lambda: os.getenv("BOT_DB_PATH", "data/krakenbot.db"))
    log_dir: str = field(default_factory=lambda: os.getenv("BOT_LOG_DIR", "data/bot_logs"))

    def __post_init__(self):
        Path(self.log_dir).mkdir(parents=True, exist_ok=True)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

    @property
    def mode_label(self) -> str:
        if self.dry_run:
            return "DRY-RUN"
        if self.demo:
            return "DEMO"
        return "LIVE"
