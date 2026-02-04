# tests/test_aggregator.py
import time
from src.models import LiquidationEvent
from src.aggregator import LiquidationAggregator


def make_event(
    coin="BTC",
    side="long",
    value_usd=10000.0,
    exchange="bybit",
    timestamp=None,
):
    return LiquidationEvent(
        exchange=exchange,
        coin=coin,
        side=side,
        size=value_usd / 90000,
        price=90000.0,
        value_usd=value_usd,
        timestamp=timestamp or int(time.time() * 1000),
        wallet=None,
    )


def test_add_event():
    agg = LiquidationAggregator()
    event = make_event()
    agg.add_event(event)
    assert len(agg.events) == 1


def test_filters_below_minimum():
    agg = LiquidationAggregator(min_value_usd=1000)
    small_event = make_event(value_usd=500)
    large_event = make_event(value_usd=5000)
    agg.add_event(small_event)
    agg.add_event(large_event)
    assert len(agg.events) == 1
    assert agg.events[0].value_usd == 5000


def test_prune_old_events():
    agg = LiquidationAggregator(window_ms=1000)  # 1 second window
    old_event = make_event(timestamp=int(time.time() * 1000) - 2000)
    new_event = make_event(timestamp=int(time.time() * 1000))
    agg.add_event(old_event)
    agg.add_event(new_event)
    agg._prune()
    assert len(agg.events) == 1


def test_top_10():
    agg = LiquidationAggregator(min_value_usd=0)
    for i in range(15):
        agg.add_event(make_event(value_usd=(i + 1) * 1000))
    top = agg.top_10()
    assert len(top) == 10
    assert top[0].value_usd == 15000
    assert top[9].value_usd == 6000


def test_by_coin():
    agg = LiquidationAggregator(min_value_usd=0)
    agg.add_event(make_event(coin="BTC", side="long", value_usd=10000))
    agg.add_event(make_event(coin="BTC", side="short", value_usd=5000))
    agg.add_event(make_event(coin="ETH", side="long", value_usd=3000))

    stats = agg.by_coin()
    assert stats["BTC"]["count"] == 2
    assert stats["BTC"]["total_usd"] == 15000
    assert stats["BTC"]["long_usd"] == 10000
    assert stats["BTC"]["short_usd"] == 5000
    assert stats["ETH"]["count"] == 1


def test_long_short_ratio():
    agg = LiquidationAggregator(min_value_usd=0)
    agg.add_event(make_event(side="long", value_usd=60000))
    agg.add_event(make_event(side="short", value_usd=40000))

    long_pct, short_pct = agg.long_short_ratio()
    assert long_pct == 60.0
    assert short_pct == 40.0


def test_by_exchange():
    agg = LiquidationAggregator(min_value_usd=0)
    agg.add_event(make_event(exchange="bybit", value_usd=10000))
    agg.add_event(make_event(exchange="binance", value_usd=5000))

    stats = agg.by_exchange()
    assert stats["bybit"]["count"] == 1
    assert stats["bybit"]["total_usd"] == 10000


def test_recent_feed():
    # Use a very large window to prevent pruning of historical timestamps
    agg = LiquidationAggregator(min_value_usd=0, window_ms=10**15)
    for i in range(20):
        agg.add_event(make_event(value_usd=(i + 1) * 100, timestamp=1700000000000 + i))

    recent = agg.recent_feed(limit=15)
    assert len(recent) == 15
    # Most recent first
    assert recent[0].timestamp == 1700000000019
