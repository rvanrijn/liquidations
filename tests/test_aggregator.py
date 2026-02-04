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
