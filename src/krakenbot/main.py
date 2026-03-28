"""TradingView webhook relay for Kraken Futures."""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import time as _time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.krakenbot.config import BotConfig
from src.krakenbot.database import KrakenBotDatabase
from src.krakenbot.executor import KrakenExecutor
from src.krakenbot.models import LivePosition, LiveTrade
from src.krakenbot.telegram import send_message as tg_send

logger = logging.getLogger("krakenbot")

# Module-level state (initialized in lifespan)
config: BotConfig | None = None
db: KrakenBotDatabase | None = None
executor: KrakenExecutor | None = None
_lock = asyncio.Lock()
_last_webhook_time: float = 0.0


def _setup_logging(cfg: BotConfig) -> None:
    """Set up file + console logging."""
    Path(cfg.log_dir).mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = logging.handlers.RotatingFileHandler(
        f"{cfg.log_dir}/webhook.log", maxBytes=5_000_000, backupCount=3,
    )
    fh.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(fh)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown."""
    global config, db, executor
    config = BotConfig()
    _setup_logging(config)

    db = KrakenBotDatabase(config.db_path)
    executor = KrakenExecutor(config, db)

    if not await executor.initialize():
        if not config.dry_run:
            raise RuntimeError("Executor failed to initialize")
        logger.warning("Executor init failed but dry-run mode — continuing")

    await executor.reconcile_position()
    logger.info("KrakenBot webhook relay started [%s]", config.mode_label)

    # Start 4h status loop
    status_task = asyncio.create_task(_status_loop())

    await _notify_trade(f"🟢 KrakenBot started [{config.mode_label}]")

    yield

    status_task.cancel()
    db.close()
    logger.info("KrakenBot webhook relay stopped")


app = FastAPI(title="KrakenBot Webhook", lifespan=lifespan)


@app.get("/")
async def health():
    """Health check."""
    pos = db.load_live_position()
    pos_label = "FLAT"
    if pos:
        pos_label = f"{pos.direction} {pos.size_contracts} BTC @ {pos.entry_price:.0f}"
    return {
        "status": "ok",
        "mode": config.mode_label,
        "position": pos_label,
        "last_webhook": _last_webhook_time,
    }


@app.post("/webhook")
async def webhook(request: Request):
    """Handle TradingView webhook."""
    global _last_webhook_time

    body = await request.json()

    # --- Auth ---
    if config.webhook_secret and body.get("secret") != config.webhook_secret:
        logger.warning("Rejected webhook: invalid secret")
        return JSONResponse(status_code=401, content={"ok": False, "error": "invalid secret"})

    # --- Skip Saturdays ---
    if datetime.now(timezone.utc).weekday() == 5:
        logger.info("Ignored webhook: Saturday")
        return {"ok": True, "action": "no_change", "detail": "Saturday — signals ignored"}

    # --- Parse desired state from position field ---
    tv_position = body.get("position", 0)
    try:
        tv_position = float(tv_position)
    except (TypeError, ValueError):
        return JSONResponse(status_code=400, content={"ok": False, "error": "invalid position field"})

    if tv_position > 0:
        desired = "LONG"
    elif tv_position < 0:
        desired = "SHORT"
    else:
        desired = "FLAT"

    price = float(body.get("price", 0))
    action_label = body.get("action", "unknown")
    comment = body.get("comment", "")

    logger.info(
        "WEBHOOK received: action=%s position=%s desired=%s price=%.2f comment=%s",
        action_label, tv_position, desired, price, comment,
    )

    # --- Serialize execution ---
    async with _lock:
        _last_webhook_time = _time()
        current_pos = db.load_live_position()
        current_dir = current_pos.direction if current_pos else "FLAT"

        if current_dir == desired:
            logger.info("No-op: already %s", desired)
            return {"ok": True, "action": "no_change", "detail": f"already {desired}"}

        # --- Close current position if any ---
        closed_trade = None
        if current_pos:
            reason = "webhook_reverse" if desired != "FLAT" else "webhook"
            closed_trade = await _close_and_log(current_pos, price, reason)
            if closed_trade is None:
                return JSONResponse(
                    status_code=500,
                    content={"ok": False, "error": "failed to close current position"},
                )

        # --- Open new position if not going flat ---
        new_pos = None
        if desired != "FLAT":
            balance = await executor.get_balance()
            if balance is None or balance <= 0:
                return JSONResponse(
                    status_code=500,
                    content={"ok": False, "error": "could not fetch balance"},
                )

            size = executor.calculate_position_size(balance, price)
            if size <= 0:
                return JSONResponse(
                    status_code=500,
                    content={"ok": False, "error": f"position size too small (balance={balance:.2f})"},
                )

            result = await executor.open_position(desired, size, price)
            if not result.success:
                action_desc = "partial_reverse" if closed_trade else "open_failed"
                return JSONResponse(
                    status_code=500,
                    content={"ok": False, "error": f"open failed: {result.error}", "action": action_desc},
                )

            new_pos = LivePosition(
                direction=desired,
                entry_price=result.filled_price or price,
                entry_time=_time(),
                size_contracts=result.filled_size or size,
                notional_usd=balance * config.leverage,
                kraken_order_id=result.order_id,
                cli_ord_id=result.cli_ord_id,
            )
            db.save_live_position(new_pos)

            logger.info(
                "OPENED %s %.4f BTC @ %.0f | notional $%.0f | order=%s",
                desired, new_pos.size_contracts, new_pos.entry_price,
                new_pos.notional_usd, result.order_id,
            )

        # --- Determine action label ---
        if closed_trade and desired != "FLAT":
            action_desc = f"reversed {current_dir} -> {desired}"
        elif closed_trade:
            action_desc = f"closed {current_dir}"
        else:
            action_desc = f"opened {desired}"

        # --- Telegram notification ---
        parts = [f"🔔 <b>{action_desc.upper()}</b>"]
        if closed_trade:
            parts.append(f"Closed {closed_trade.direction} @ {closed_trade.exit_price:,.0f}")
            parts.append(f"PnL: ${closed_trade.pnl_usd:+,.2f} ({closed_trade.move_pct:+.2f}%)")
        if desired != "FLAT" and new_pos:
            parts.append(f"Opened {desired} @ {price:,.0f}")
            parts.append(f"Size: {new_pos.size_contracts} BTC | Notional: ${new_pos.notional_usd:,.0f}")
        bal = closed_trade.balance_after if closed_trade else (await executor.get_balance() or 0)
        if bal:
            parts.append(f"Balance: ${bal:,.2f}")
        await _notify_trade("\n".join(parts))

        return {"ok": True, "action": action_desc, "detail": comment or action_label}


async def _close_and_log(
    position: LivePosition,
    exit_price: float,
    reason: str,
) -> LiveTrade | None:
    """Close position, compute P&L, log trade, clear DB position."""
    result = await executor.close_position(
        position.direction, position.size_contracts, exit_price, reason,
    )
    if not result.success:
        logger.error("Failed to close position: %s", result.error)
        return None

    if position.direction == "LONG":
        move_pct = (exit_price - position.entry_price) / position.entry_price * 100
    else:
        move_pct = (position.entry_price - exit_price) / position.entry_price * 100

    pnl = move_pct / 100 * position.notional_usd
    fee = result.fee_usd

    # In dry-run, get_balance() returns last trade's balance_after (stale).
    # Always compute balance_after from the previous balance + current P&L.
    prev_balance = db.get_live_balance()
    if prev_balance is None:
        prev_balance = await executor.get_balance() or 1000.0
    balance_after = prev_balance + pnl - fee

    trade = LiveTrade(
        direction=position.direction,
        entry_price=position.entry_price,
        exit_price=exit_price,
        entry_time=position.entry_time,
        exit_time=_time(),
        size_contracts=position.size_contracts,
        notional_usd=position.notional_usd,
        pnl_usd=pnl,
        fee_usd=fee,
        balance_after=balance_after,
        move_pct=move_pct,
        exit_reason=reason,
        entry_order_id=position.kraken_order_id,
        exit_order_id=result.order_id,
    )

    db.log_live_trade(trade)
    db.clear_live_position()
    logger.info(
        "CLOSED %s @ %.0f | PnL $%+.2f | Balance $%.2f | %s",
        trade.direction, exit_price, pnl, balance_after, reason,
    )
    return trade


async def _notify_trade(text: str) -> None:
    """Send a Telegram notification (no-op if not configured)."""
    await tg_send(config.telegram_bot_token, config.telegram_chat_id, text)


async def _status_loop() -> None:
    """Send position status via Telegram every 4 hours."""
    while True:
        await asyncio.sleep(4 * 3600)
        try:
            pos = db.load_live_position()
            balance = await executor.get_balance()
            if balance is None:
                balance = db.get_live_balance() or 0

            lines = [f"📊 <b>KrakenBot Status</b>", f"Mode: {config.mode_label}"]

            if pos:
                # Estimate current price from balance calc
                if not config.dry_run:
                    positions = await executor.get_open_positions()
                    current_price = pos.entry_price  # fallback
                    for p in positions:
                        if p.get("symbol") == config.symbol:
                            current_price = float(p.get("markPrice", pos.entry_price))
                            break
                else:
                    current_price = pos.entry_price

                if pos.direction == "LONG":
                    pnl_pct = (current_price - pos.entry_price) / pos.entry_price * 100
                else:
                    pnl_pct = (pos.entry_price - current_price) / pos.entry_price * 100
                pnl_usd = pnl_pct / 100 * pos.notional_usd

                lines.append(f"Position: {pos.direction} {pos.size_contracts} BTC @ {pos.entry_price:,.0f}")
                lines.append(f"Price: {current_price:,.0f}")
                lines.append(f"PnL: ${pnl_usd:+,.2f} ({pnl_pct:+.2f}%)")
            else:
                lines.append("Position: FLAT")

            lines.append(f"Balance: ${balance:,.2f}")

            await _notify_trade("\n".join(lines))
        except Exception as e:
            logger.warning("Status loop error: %s", e)


def main():
    """Sync entry point."""
    cfg = BotConfig()
    _setup_logging(cfg)
    uvicorn.run(
        "src.krakenbot.main:app",
        host=cfg.webhook_host,
        port=cfg.webhook_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
