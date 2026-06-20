from src.radar.feeds import RadarLiqFeed


def _force_order(side, qty, ap):
    return {"o": {"S": side, "z": str(qty), "ap": str(ap)}}


def test_callback_fires_with_mapped_side_and_price():
    events = []
    feed = RadarLiqFeed(on_event=lambda ts, side, usd, price: events.append((side, usd, price)))
    feed._on_message(_force_order("SELL", 2.0, 60000.0))   # long liquidated
    feed._on_message(_force_order("BUY", 1.0, 61000.0))    # short liquidated
    assert events[0] == ("long", 120000.0, 60000.0)
    assert events[1] == ("short", 61000.0, 61000.0)


def test_callback_skips_zero_qty_or_price():
    events = []
    feed = RadarLiqFeed(on_event=lambda *a: events.append(a))
    feed._on_message(_force_order("SELL", 0, 60000.0))
    feed._on_message(_force_order("BUY", 1.0, 0))
    assert events == []


def test_base_totals_still_populated():
    feed = RadarLiqFeed(on_event=None)
    feed._on_message(_force_order("SELL", 2.0, 60000.0))
    long_usd, short_usd, _rate = feed.totals()
    assert long_usd == 120000.0
    assert short_usd == 0.0
