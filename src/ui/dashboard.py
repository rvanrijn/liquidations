# src/ui/dashboard.py
from datetime import datetime


def format_usd(value: float) -> str:
    """Format USD value with K/M suffix."""
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    elif value >= 1_000:
        return f"${value / 1_000:.1f}K"
    else:
        return f"${value:.0f}"


def format_time(timestamp_ms: int) -> str:
    """Format timestamp to HH:MM:SS."""
    dt = datetime.fromtimestamp(timestamp_ms / 1000)
    return dt.strftime("%H:%M:%S")


def truncate_wallet(wallet: str | None, length: int = 6) -> str:
    """Truncate wallet address for display."""
    if not wallet:
        return "-"
    if len(wallet) <= length + 3:
        return wallet
    return f"{wallet[:length]}..."
