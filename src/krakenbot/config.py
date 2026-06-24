# src/krakenbot/config.py
"""Bot configuration — AWS Secrets Manager first, .env fallback."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# AWS secret name — set via env var or default
_AWS_SECRET_ID = os.getenv("AWS_SECRET_ID", "krakenbot/secrets")
_AWS_REGION = os.getenv("AWS_REGION", "us-east-1")


def _load_aws_secrets() -> dict:
    """Try loading secrets from AWS Secrets Manager. Returns empty dict on failure."""
    try:
        import boto3
        client = boto3.client("secretsmanager", region_name=_AWS_REGION)
        resp = client.get_secret_value(SecretId=_AWS_SECRET_ID)
        secrets = json.loads(resp["SecretString"])
        logger.info("Loaded secrets from AWS Secrets Manager (%s)", _AWS_SECRET_ID)
        return secrets
    except Exception:
        return {}


# Load once at import time — env vars override AWS values
_aws = _load_aws_secrets()


def _get(key: str, default: str = "") -> str:
    """Get config value: env var > AWS secret > default."""
    return os.getenv(key) or _aws.get(key, default)


@dataclass
class BotConfig:
    """All bot settings, loaded from env vars with safe defaults."""

    # Kraken credentials
    kraken_api_key: str = field(default_factory=lambda: _get("KRAKEN_FUTURES_API_KEY"))
    kraken_api_secret: str = field(default_factory=lambda: _get("KRAKEN_FUTURES_API_SECRET"))

    # Mode flags
    demo: bool = field(default_factory=lambda: _get("KRAKEN_DEMO", "true").lower() == "true")
    dry_run: bool = field(default_factory=lambda: _get("BOT_DRY_RUN", "true").lower() == "true")

    # Trading parameters
    symbol: str = field(default_factory=lambda: _get("KRAKEN_SYMBOL", "PF_XBTUSD"))
    leverage: int = field(default_factory=lambda: int(_get("BOT_LEVERAGE", "3")))

    # Webhook
    webhook_secret: str = field(default_factory=lambda: _get("WEBHOOK_SECRET"))
    webhook_port: int = field(default_factory=lambda: int(_get("WEBHOOK_PORT", "8080")))
    webhook_host: str = field(default_factory=lambda: _get("WEBHOOK_HOST", "0.0.0.0"))

    # Telegram
    telegram_bot_token: str = field(default_factory=lambda: _get("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str = field(default_factory=lambda: _get("TELEGRAM_CHAT_ID"))

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
