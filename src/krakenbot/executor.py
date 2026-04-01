# src/krakenbot/executor.py
"""Kraken Futures SDK wrapper — handles order execution, position management."""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from time import time

from src.krakenbot.config import BotConfig
from src.krakenbot.database import KrakenBotDatabase

logger = logging.getLogger(__name__)

TAKER_FEE_PCT = 0.0005  # 0.05% taker fee (IoC = taker)


@dataclass
class OrderResult:
    """Result of an order attempt."""

    success: bool
    order_id: str
    cli_ord_id: str
    filled_price: float
    filled_size: float
    fee_usd: float
    error: str = ""
    dry_run: bool = False


class KrakenExecutor:
    """Wraps the Kraken Futures SDK for async order execution."""

    def __init__(self, config: BotConfig, db: KrakenBotDatabase):
        self.config = config
        self.db = db
        self._trade_client = None
        self._user_client = None
        self._initialized = False

    async def initialize(self) -> bool:
        """Set up SDK clients and verify connectivity."""
        if self.config.dry_run:
            logger.info("Executor running in DRY-RUN mode — no real orders will be sent")
            self._initialized = True
            return True

        try:
            from kraken.futures import Trade, User

            url = "https://demo-futures.kraken.com" if self.config.demo else "https://futures.kraken.com"

            self._trade_client = Trade(
                key=self.config.kraken_api_key,
                secret=self.config.kraken_api_secret,
                url=url,
            )
            self._user_client = User(
                key=self.config.kraken_api_key,
                secret=self.config.kraken_api_secret,
                url=url,
            )

            # Verify connectivity — get balance
            balance = await self._async_get_balance()
            if balance is None:
                logger.error("Failed to get balance from Kraken — check credentials")
                return False

            logger.info(
                "Kraken Executor initialized [%s] — balance: $%.2f",
                self.config.mode_label, balance,
            )

            # Set leverage
            await self._async_set_leverage()

            self._initialized = True
            return True

        except ImportError:
            logger.error("python-kraken-sdk not installed. Run: pip install python-kraken-sdk")
            return False
        except Exception as e:
            logger.error("Executor initialization failed: %s", e)
            return False

    async def get_balance(self) -> float | None:
        """Get available USD balance."""
        if self.config.dry_run:
            # In dry-run, use DB balance or return a simulated value
            db_balance = self.db.get_live_balance()
            return db_balance if db_balance else 1000.0

        return await self._async_get_balance()

    async def _async_get_balance(self) -> float | None:
        """Get balance via Kraken SDK (wrapped for async)."""
        try:
            result = await asyncio.to_thread(self._user_client.get_wallets)
            logger.warning("Kraken wallet response: %s", result)
            # Navigate Kraken response to find flex/cash balance
            accounts = result.get("accounts", {})
            # Try multi-collateral flex account first
            flex = accounts.get("flex", {})
            if flex:
                balance = flex.get("availableMargin", flex.get("availableBalance", 0))
                if balance:
                    return float(balance)
            # Try legacy fi_xbtusd account
            fi = accounts.get("fi_xbtusd", {})
            if fi:
                balance = fi.get("availableBalance", fi.get("auxiliary", {}).get("usd", 0))
                if balance:
                    return float(balance)
            # Fallback: try all accounts
            for acct_name, acct in accounts.items():
                if isinstance(acct, dict):
                    for key in ("availableMargin", "availableBalance", "balance"):
                        val = acct.get(key, 0)
                        if val and float(val) > 0:
                            logger.info("Found balance in %s.%s: %s", acct_name, key, val)
                            return float(val)
            return 0.0
        except Exception as e:
            logger.error("Balance fetch failed: %s", e)
            return None

    async def _async_set_leverage(self) -> None:
        """Leverage is configured via Kraken web UI — no SDK method available."""
        logger.info("Leverage %dx (set via Kraken UI, not API)", self.config.leverage)

    def calculate_position_size(self, balance: float, entry_price: float) -> float:
        """Calculate position size in BTC (PF_XBTUSD: size is in BTC)."""
        notional = balance * self.config.leverage * 0.90  # 10% buffer for fees/margin
        qty_btc = notional / entry_price
        # Round down to 4 decimal places (0.0001 BTC minimum step)
        qty_btc = int(qty_btc * 10000) / 10000
        if qty_btc < 0.0001:
            logger.warning("Position size too small: %.6f BTC < 0.0001 minimum", qty_btc)
            return 0.0
        return qty_btc

    async def open_position(self, direction: str, size: float, entry_price: float) -> OrderResult:
        """Open a position via market order (IoC limit at current price)."""
        cli_ord_id = f"kb_{uuid.uuid4().hex[:12]}"
        side = "buy" if direction == "LONG" else "sell"
        now = time()

        if self.config.dry_run:
            result = OrderResult(
                success=True,
                order_id=f"dry_{cli_ord_id}",
                cli_ord_id=cli_ord_id,
                filled_price=entry_price,
                filled_size=size,
                fee_usd=size * entry_price * TAKER_FEE_PCT,  # fee on notional
                dry_run=True,
            )
            self.db.log_order(
                timestamp=now, side=side, order_type="market",
                size=size, price=entry_price, cli_ord_id=cli_ord_id,
                kraken_order_id=result.order_id, status="filled_dry",
                error="", dry_run=True,
            )
            logger.info(
                "DRY-RUN OPEN %s %s %.2f contracts @ $%.0f | cli=%s",
                direction, self.config.symbol, size, entry_price, cli_ord_id,
            )
            return result

        try:
            resp = await asyncio.to_thread(
                self._trade_client.create_order,
                orderType="mkt",
                symbol=self.config.symbol,
                side=side,
                size=size,
                cliOrdId=cli_ord_id,
            )

            status = resp.get("sendStatus", {})
            order_id = status.get("order_id", "")
            order_status = status.get("status", "error")

            if order_status in ("placed", "partiallyFilled", "filled"):
                # Fetch actual fill price from Kraken
                fill_price, fill_size = await self._get_fill_price(order_id, entry_price)
                actual_price = fill_price if fill_price > 0 else entry_price
                actual_size = fill_size if fill_size > 0 else size
                result = OrderResult(
                    success=True,
                    order_id=order_id,
                    cli_ord_id=cli_ord_id,
                    filled_price=actual_price,
                    filled_size=actual_size,
                    fee_usd=actual_size * actual_price * TAKER_FEE_PCT,
                )
                self.db.log_order(
                    timestamp=now, side=side, order_type="market",
                    size=actual_size, price=actual_price, cli_ord_id=cli_ord_id,
                    kraken_order_id=order_id, status=order_status,
                    error="", dry_run=False,
                )
                logger.info(
                    "OPEN %s %s %.4f @ $%.2f | order=%s",
                    direction, self.config.symbol, actual_size, actual_price, order_id,
                )
                return result
            else:
                error_msg = status.get("reason", "unknown")
                self.db.log_order(
                    timestamp=now, side=side, order_type="market",
                    size=size, price=entry_price, cli_ord_id=cli_ord_id,
                    kraken_order_id=order_id, status=order_status,
                    error=error_msg, dry_run=False,
                )
                logger.error("Order rejected: %s", error_msg)
                return OrderResult(
                    success=False, order_id=order_id, cli_ord_id=cli_ord_id,
                    filled_price=0, filled_size=0, fee_usd=0, error=error_msg,
                )

        except Exception as e:
            self.db.log_order(
                timestamp=now, side=side, order_type="market",
                size=size, price=entry_price, cli_ord_id=cli_ord_id,
                kraken_order_id="", status="exception",
                error=str(e), dry_run=False,
            )
            logger.error("Order exception: %s", e)
            return OrderResult(
                success=False, order_id="", cli_ord_id=cli_ord_id,
                filled_price=0, filled_size=0, fee_usd=0, error=str(e),
            )

    async def place_limit_order(self, direction: str, size: float, limit_price: float) -> OrderResult:
        """Place a limit order at the given price."""
        cli_ord_id = f"kl_{uuid.uuid4().hex[:12]}"
        side = "buy" if direction == "LONG" else "sell"
        now = time()

        if self.config.dry_run:
            result = OrderResult(
                success=True,
                order_id=f"dry_{cli_ord_id}",
                cli_ord_id=cli_ord_id,
                filled_price=0,
                filled_size=0,
                fee_usd=0,
                dry_run=True,
            )
            self.db.log_order(
                timestamp=now, side=side, order_type="limit",
                size=size, price=limit_price, cli_ord_id=cli_ord_id,
                kraken_order_id=result.order_id, status="placed_dry",
                error="", dry_run=True,
            )
            logger.info(
                "DRY-RUN LIMIT %s %.4f @ $%.0f | cli=%s",
                direction, size, limit_price, cli_ord_id,
            )
            return result

        try:
            resp = await asyncio.to_thread(
                self._trade_client.create_order,
                orderType="lmt",
                symbol=self.config.symbol,
                side=side,
                size=size,
                limitPrice=limit_price,
                cliOrdId=cli_ord_id,
            )

            status = resp.get("sendStatus", {})
            order_id = status.get("order_id", "")
            order_status = status.get("status", "error")

            if order_status in ("placed", "partiallyFilled", "filled"):
                # Check if immediately filled
                filled_price = 0.0
                filled_size = 0.0
                for evt in status.get("orderEvents", []):
                    if evt.get("type") == "EXECUTION":
                        filled_price = float(evt.get("price", 0))
                        filled_size = float(evt.get("amount", 0))

                result = OrderResult(
                    success=True,
                    order_id=order_id,
                    cli_ord_id=cli_ord_id,
                    filled_price=filled_price,
                    filled_size=filled_size,
                    fee_usd=filled_size * (filled_price or limit_price) * TAKER_FEE_PCT if filled_size else 0,
                )
                self.db.log_order(
                    timestamp=now, side=side, order_type="limit",
                    size=size, price=limit_price, cli_ord_id=cli_ord_id,
                    kraken_order_id=order_id, status=order_status,
                    error="", dry_run=False,
                )
                logger.info(
                    "LIMIT %s %.4f @ $%.0f | order=%s | filled=%.4f",
                    direction, size, limit_price, order_id, filled_size,
                )
                return result
            else:
                error_msg = status.get("reason", "unknown")
                self.db.log_order(
                    timestamp=now, side=side, order_type="limit",
                    size=size, price=limit_price, cli_ord_id=cli_ord_id,
                    kraken_order_id=order_id, status=order_status,
                    error=error_msg, dry_run=False,
                )
                logger.error("Limit order rejected: %s", error_msg)
                return OrderResult(
                    success=False, order_id=order_id, cli_ord_id=cli_ord_id,
                    filled_price=0, filled_size=0, fee_usd=0, error=error_msg,
                )

        except Exception as e:
            self.db.log_order(
                timestamp=now, side=side, order_type="limit",
                size=size, price=limit_price, cli_ord_id=cli_ord_id,
                kraken_order_id="", status="exception",
                error=str(e), dry_run=False,
            )
            logger.error("Limit order exception: %s", e)
            return OrderResult(
                success=False, order_id="", cli_ord_id=cli_ord_id,
                filled_price=0, filled_size=0, fee_usd=0, error=str(e),
            )

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order. Returns True if cancelled successfully."""
        if self.config.dry_run:
            logger.info("DRY-RUN CANCEL order %s", order_id)
            return True

        try:
            resp = await asyncio.to_thread(
                self._trade_client.cancel_order,
                order_id=order_id,
            )
            status = resp.get("cancelStatus", {}).get("status", "")
            if status in ("cancelled", "notFound"):
                logger.info("Cancelled order %s (status=%s)", order_id, status)
                return True
            logger.warning("Cancel order %s got status: %s", order_id, resp)
            return False
        except Exception as e:
            logger.error("Cancel order exception: %s", e)
            return False

    async def _get_fill_price(self, order_id: str, fallback_price: float) -> tuple[float, float]:
        """Query Kraken for actual fill price and size. Returns (price, size)."""
        # Small delay to let Kraken settle the fill
        await asyncio.sleep(1)
        try:
            resp = await asyncio.to_thread(
                self._trade_client.get_orders_status,
                orderIds=[order_id],
            )
            logger.info("Order status response: %s", resp)
            orders = resp.get("orders", [])
            if orders:
                order = orders[0]
                filled_size = float(order.get("filled", 0))
                fill_price = 0.0

                # Method 1: averagePrice field (most reliable for market orders)
                avg_price = order.get("averagePrice") or order.get("avgPrice")
                if avg_price:
                    fill_price = float(avg_price)

                # Method 2: execution events
                if fill_price == 0:
                    for evt in order.get("orderEvents", []):
                        if evt.get("type") == "EXECUTION":
                            fill_price = float(evt.get("price", 0))
                            filled_size = float(evt.get("amount", filled_size))

                # Method 3: lastPrice field
                if fill_price == 0:
                    last_price = order.get("lastPrice")
                    if last_price:
                        fill_price = float(last_price)

                if fill_price > 0 and filled_size > 0:
                    slippage = (fill_price - fallback_price) / fallback_price * 100
                    logger.info(
                        "Fill price: $%.2f (ref $%.2f, slippage %+.3f%%)",
                        fill_price, fallback_price, slippage,
                    )
                    return fill_price, filled_size
                elif filled_size > 0:
                    logger.warning("Got filled_size=%.4f but no fill price — using fallback", filled_size)
                    return fallback_price, filled_size
        except Exception as e:
            logger.warning("Could not fetch fill price: %s", e)
        return fallback_price, 0.0

    async def check_order_filled(self, order_id: str) -> tuple[bool, float, float]:
        """Check if a limit order has been filled. Returns (filled, price, size)."""
        if self.config.dry_run:
            return False, 0.0, 0.0

        try:
            resp = await asyncio.to_thread(
                self._trade_client.get_orders_status,
                orderIds=[order_id],
            )
            orders = resp.get("orders", [])
            if not orders:
                return False, 0.0, 0.0
            order = orders[0]
            filled = float(order.get("filled", 0))
            qty = float(order.get("quantity", 0))
            if filled >= qty and filled > 0:
                # Fully filled — get price from last fill
                price = float(order.get("limitPrice", 0))
                return True, price, filled
            return False, 0.0, 0.0
        except Exception as e:
            logger.error("Check order status failed: %s", e)
            return False, 0.0, 0.0

    async def close_position(self, direction: str, size: float, exit_price: float, reason: str) -> OrderResult:
        """Close a position via reduceOnly market order."""
        cli_ord_id = f"kc_{uuid.uuid4().hex[:12]}"
        # Opposite side to close
        side = "sell" if direction == "LONG" else "buy"
        now = time()

        if self.config.dry_run:
            result = OrderResult(
                success=True,
                order_id=f"dry_{cli_ord_id}",
                cli_ord_id=cli_ord_id,
                filled_price=exit_price,
                filled_size=size,
                fee_usd=size * exit_price * TAKER_FEE_PCT,
                dry_run=True,
            )
            self.db.log_order(
                timestamp=now, side=side, order_type="market_reduce",
                size=size, price=exit_price, cli_ord_id=cli_ord_id,
                kraken_order_id=result.order_id, status="filled_dry",
                error="", dry_run=True,
            )
            logger.info(
                "DRY-RUN CLOSE %s %.2f @ $%.0f | reason=%s | cli=%s",
                direction, size, exit_price, reason, cli_ord_id,
            )
            return result

        try:
            resp = await asyncio.to_thread(
                self._trade_client.create_order,
                orderType="mkt",
                symbol=self.config.symbol,
                side=side,
                size=size,
                cliOrdId=cli_ord_id,
                reduceOnly=True,
            )

            status = resp.get("sendStatus", {})
            order_id = status.get("order_id", "")
            order_status = status.get("status", "error")

            if order_status in ("placed", "partiallyFilled", "filled"):
                # Fetch actual fill price from Kraken
                fill_price, fill_size = await self._get_fill_price(order_id, exit_price)
                actual_price = fill_price if fill_price > 0 else exit_price
                actual_size = fill_size if fill_size > 0 else size
                result = OrderResult(
                    success=True,
                    order_id=order_id,
                    cli_ord_id=cli_ord_id,
                    filled_price=actual_price,
                    filled_size=actual_size,
                    fee_usd=actual_size * actual_price * TAKER_FEE_PCT,
                )
                self.db.log_order(
                    timestamp=now, side=side, order_type="market_reduce",
                    size=actual_size, price=actual_price, cli_ord_id=cli_ord_id,
                    kraken_order_id=order_id, status=order_status,
                    error="", dry_run=False,
                )
                logger.info(
                    "CLOSE %s %.4f @ $%.2f | reason=%s | order=%s",
                    direction, actual_size, actual_price, reason, order_id,
                )
                return result
            else:
                error_msg = status.get("reason", "unknown")
                self.db.log_order(
                    timestamp=now, side=side, order_type="market_reduce",
                    size=size, price=exit_price, cli_ord_id=cli_ord_id,
                    kraken_order_id=order_id, status=order_status,
                    error=error_msg, dry_run=False,
                )
                logger.error("Close order rejected: %s", error_msg)
                return OrderResult(
                    success=False, order_id=order_id, cli_ord_id=cli_ord_id,
                    filled_price=0, filled_size=0, fee_usd=0, error=error_msg,
                )

        except Exception as e:
            self.db.log_order(
                timestamp=now, side=side, order_type="market_reduce",
                size=size, price=exit_price, cli_ord_id=cli_ord_id,
                kraken_order_id="", status="exception",
                error=str(e), dry_run=False,
            )
            logger.error("Close order exception: %s", e)
            return OrderResult(
                success=False, order_id="", cli_ord_id=cli_ord_id,
                filled_price=0, filled_size=0, fee_usd=0, error=str(e),
            )

    async def get_open_positions(self) -> list[dict]:
        """Get currently open positions from Kraken."""
        if self.config.dry_run:
            return []
        try:
            result = await asyncio.to_thread(self._user_client.get_open_positions)
            return result.get("openPositions", [])
        except Exception as e:
            logger.error("Get positions failed: %s", e)
            return []

    async def reconcile_position(self) -> None:
        """On startup, reconcile DB position with Kraken's actual state."""
        if self.config.dry_run:
            return

        db_pos = self.db.load_live_position()
        kraken_positions = await self.get_open_positions()

        # Find our symbol's position
        kraken_pos = None
        for p in kraken_positions:
            if p.get("symbol") == self.config.symbol:
                kraken_pos = p
                break

        if db_pos and not kraken_pos:
            logger.warning(
                "DB has position but Kraken doesn't — clearing stale DB position"
            )
            self.db.clear_live_position()
        elif kraken_pos and not db_pos:
            logger.warning(
                "Kraken has position but DB doesn't — manual intervention needed. "
                "Position: %s", kraken_pos,
            )
