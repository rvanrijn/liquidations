"""Telegram notifications for KrakenBot."""

from __future__ import annotations

import logging

import aiohttp

logger = logging.getLogger("krakenbot")

API_URL = "https://api.telegram.org/bot{token}/sendMessage"


async def send_message(token: str, chat_id: str, text: str) -> bool:
    """Send a Telegram message. Returns True on success."""
    if not token or not chat_id:
        return False
    try:
        async with aiohttp.ClientSession() as session:
            resp = await session.post(
                API_URL.format(token=token),
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                timeout=aiohttp.ClientTimeout(total=10),
            )
            if resp.status != 200:
                body = await resp.text()
                logger.warning("Telegram send failed (%d): %s", resp.status, body)
                return False
            return True
    except Exception as e:
        logger.warning("Telegram send error: %s", e)
        return False
