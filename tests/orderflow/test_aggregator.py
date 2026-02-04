# tests/orderflow/test_aggregator.py
import time
from src.orderflow.models import TradeEvent
from src.orderflow.aggregator import OrderFlowAggregator


def make_trade_event(
    coin="BTC",
    side="buy",
    value_usd=10000.0,
    exchange="bybit",
    timestamp=None,
    size_category=None,
):
    """Helper function to create TradeEvent instances for testing."""
    price = 90000.0 if coin == "BTC" else 3400.0
    size = value_usd / price
    if size_category is None:
        # Determine category based on value
        if value_usd >= 1_000_000:
            size_category = "whale"
        elif value_usd >= 100_000:
            size_category = "large"
        elif value_usd >= 10_000:
            size_category = "medium"
        else:
            size_category = "small"
    return TradeEvent(
        exchange=exchange,
        coin=coin,
        side=side,
        size=size,
        price=price,
        value_usd=value_usd,
        timestamp=timestamp or int(time.time() * 1000),
        size_category=size_category,
    )


class TestOrderFlowAggregatorInit:
    """Tests for OrderFlowAggregator initialization."""

    def test_default_initialization(self):
        """Test aggregator initializes with default values."""
        agg = OrderFlowAggregator()
        assert len(agg._events) == 0
        assert agg._window_ms > 0  # Has a default window

    def test_custom_window(self):
        """Test aggregator with custom window."""
        agg = OrderFlowAggregator(window_minutes=1)
        assert agg._window_ms == 60000  # 1 minute in ms


class TestClassifySize:
    """Tests for _classify_size() method."""

    def test_classify_whale(self):
        """Test $1.5M is classified as whale."""
        agg = OrderFlowAggregator()
        assert agg._classify_size(1_500_000) == "whale"

    def test_classify_large(self):
        """Test $500K is classified as large."""
        agg = OrderFlowAggregator()
        assert agg._classify_size(500_000) == "large"

    def test_classify_medium(self):
        """Test $50K is classified as medium."""
        agg = OrderFlowAggregator()
        assert agg._classify_size(50_000) == "medium"

    def test_classify_small(self):
        """Test $5K is classified as small."""
        agg = OrderFlowAggregator()
        assert agg._classify_size(5_000) == "small"

    def test_classify_whale_boundary(self):
        """Test exactly $1M is classified as whale."""
        agg = OrderFlowAggregator()
        assert agg._classify_size(1_000_000) == "whale"

    def test_classify_large_boundary(self):
        """Test exactly $100K is classified as large."""
        agg = OrderFlowAggregator()
        assert agg._classify_size(100_000) == "large"

    def test_classify_medium_boundary(self):
        """Test exactly $10K is classified as medium."""
        agg = OrderFlowAggregator()
        assert agg._classify_size(10_000) == "medium"


class TestAddEvent:
    """Tests for add_event() method."""

    def test_add_event_stores_event(self):
        """Test add_event stores the event."""
        agg = OrderFlowAggregator()
        event = make_trade_event()
        agg.add_event(event)
        assert len(agg._events) == 1

    def test_add_multiple_events(self):
        """Test adding multiple events."""
        agg = OrderFlowAggregator()
        for i in range(5):
            agg.add_event(make_trade_event(value_usd=(i + 1) * 1000))
        assert len(agg._events) == 5


class TestPrune:
    """Tests for _prune() method."""

    def test_prune_removes_old_events(self):
        """Test _prune() removes events outside the window."""
        agg = OrderFlowAggregator(window_minutes=1)  # 1 minute window = 60000ms
        # Add event that's 2 minutes old
        old_event = make_trade_event(timestamp=int(time.time() * 1000) - 120000)
        # Add current event
        new_event = make_trade_event(timestamp=int(time.time() * 1000))
        agg.add_event(old_event)
        agg.add_event(new_event)
        agg._prune()
        assert len(agg._events) == 1
        assert agg._events[0].timestamp == new_event.timestamp

    def test_prune_keeps_recent_events(self):
        """Test _prune() keeps events within the window."""
        agg = OrderFlowAggregator(window_minutes=1)  # 1 minute window
        now = int(time.time() * 1000)
        event1 = make_trade_event(timestamp=now - 30000)  # 30 seconds ago
        event2 = make_trade_event(timestamp=now)
        agg.add_event(event1)
        agg.add_event(event2)
        agg._prune()
        assert len(agg._events) == 2


class TestByExchange:
    """Tests for by_exchange() method."""

    def test_by_exchange_aggregates_correctly(self):
        """Test by_exchange() returns correct aggregation."""
        agg = OrderFlowAggregator()
        agg.add_event(make_trade_event(exchange="bybit", side="buy", value_usd=10000))
        agg.add_event(make_trade_event(exchange="bybit", side="buy", value_usd=20000))
        agg.add_event(make_trade_event(exchange="binance", side="sell", value_usd=15000))

        stats = agg.by_exchange()
        assert stats["bybit"]["count"] == 2
        assert stats["bybit"]["buy_usd"] == 30000
        assert stats["binance"]["count"] == 1
        assert stats["binance"]["sell_usd"] == 15000

    def test_by_exchange_empty(self):
        """Test by_exchange() with no events."""
        agg = OrderFlowAggregator()
        stats = agg.by_exchange()
        assert stats == {}


class TestBySize:
    """Tests for by_size() method."""

    def test_by_size_aggregates_correctly(self):
        """Test by_size() returns correct aggregation."""
        agg = OrderFlowAggregator()
        agg.add_event(make_trade_event(side="buy", value_usd=1_500_000))  # whale
        agg.add_event(make_trade_event(side="buy", value_usd=500_000))  # large
        agg.add_event(make_trade_event(side="buy", value_usd=50_000))  # medium
        agg.add_event(make_trade_event(side="buy", value_usd=5_000))  # small

        stats = agg.by_size()
        assert stats["whale"]["count"] == 1
        assert stats["whale"]["buy_usd"] == 1_500_000
        assert stats["large"]["count"] == 1
        assert stats["large"]["buy_usd"] == 500_000
        assert stats["medium"]["count"] == 1
        assert stats["medium"]["buy_usd"] == 50_000
        assert stats["small"]["count"] == 1
        assert stats["small"]["buy_usd"] == 5_000

    def test_by_size_multiple_in_category(self):
        """Test by_size() with multiple events in same category."""
        agg = OrderFlowAggregator()
        agg.add_event(make_trade_event(side="buy", value_usd=200_000))  # large
        agg.add_event(make_trade_event(side="buy", value_usd=300_000))  # large
        agg.add_event(make_trade_event(side="buy", value_usd=400_000))  # large

        stats = agg.by_size()
        assert stats["large"]["count"] == 3
        assert stats["large"]["buy_usd"] == 900_000


class TestTotals:
    """Tests for totals() method."""

    def test_totals_returns_correct_sums(self):
        """Test totals() returns correct sums."""
        agg = OrderFlowAggregator()
        agg.add_event(make_trade_event(side="buy", value_usd=10000))
        agg.add_event(make_trade_event(side="buy", value_usd=20000))
        agg.add_event(make_trade_event(side="sell", value_usd=15000))

        buy_usd, sell_usd, delta = agg.totals()
        assert buy_usd == 30000
        assert sell_usd == 15000
        assert delta == 15000  # buy - sell

    def test_totals_empty(self):
        """Test totals() with no events."""
        agg = OrderFlowAggregator()
        buy_usd, sell_usd, delta = agg.totals()
        assert buy_usd == 0
        assert sell_usd == 0
        assert delta == 0


class TestBuySellPressure:
    """Tests for buy_sell_pressure() method."""

    def test_buy_sell_pressure_returns_percentages(self):
        """Test buy_sell_pressure() returns correct percentages."""
        agg = OrderFlowAggregator()
        agg.add_event(make_trade_event(side="buy", value_usd=60000))
        agg.add_event(make_trade_event(side="sell", value_usd=40000))

        buy_pct, sell_pct = agg.buy_sell_pressure()
        assert buy_pct == 60.0
        assert sell_pct == 40.0

    def test_buy_sell_pressure_all_buys(self):
        """Test buy_sell_pressure() with only buy trades."""
        agg = OrderFlowAggregator()
        agg.add_event(make_trade_event(side="buy", value_usd=50000))
        agg.add_event(make_trade_event(side="buy", value_usd=50000))

        buy_pct, sell_pct = agg.buy_sell_pressure()
        assert buy_pct == 100.0
        assert sell_pct == 0.0

    def test_buy_sell_pressure_empty(self):
        """Test buy_sell_pressure() with no events returns 0/0."""
        agg = OrderFlowAggregator()
        buy_pct, sell_pct = agg.buy_sell_pressure()
        assert buy_pct == 0.0
        assert sell_pct == 0.0


class TestRecentFeed:
    """Tests for recent_feed() method."""

    def test_recent_feed_returns_large_trades_only(self):
        """Test recent_feed() returns only trades >= $100K."""
        agg = OrderFlowAggregator()

        # Add trades of various sizes (all with current timestamps)
        agg.add_event(make_trade_event(value_usd=50_000))  # excluded
        agg.add_event(make_trade_event(value_usd=100_000))  # included
        agg.add_event(make_trade_event(value_usd=150_000))  # included
        agg.add_event(make_trade_event(value_usd=80_000))  # excluded
        agg.add_event(make_trade_event(value_usd=200_000))  # included

        feed = agg.recent_feed()
        assert len(feed) == 3
        # All should be >= 100K
        for event in feed:
            assert event.value_usd >= 100_000

    def test_recent_feed_newest_first(self):
        """Test recent_feed() returns events newest first."""
        agg = OrderFlowAggregator()
        now = int(time.time() * 1000)

        agg.add_event(make_trade_event(value_usd=150_000, timestamp=now - 2000))  # oldest
        agg.add_event(make_trade_event(value_usd=200_000, timestamp=now - 1000))  # middle
        agg.add_event(make_trade_event(value_usd=100_000, timestamp=now))  # newest

        feed = agg.recent_feed()
        assert len(feed) == 3
        # Should be in reverse chronological order (newest first)
        assert feed[0].timestamp == now
        assert feed[1].timestamp == now - 1000
        assert feed[2].timestamp == now - 2000

    def test_recent_feed_respects_limit(self):
        """Test recent_feed() respects the n parameter."""
        agg = OrderFlowAggregator()
        now = int(time.time() * 1000)

        for i in range(20):
            agg.add_event(make_trade_event(value_usd=150_000, timestamp=now + i))

        feed = agg.recent_feed(n=10)
        assert len(feed) == 10
        # Most recent should be first
        assert feed[0].timestamp == now + 19


class TestMinimumThreshold:
    """Tests for minimum threshold filtering in aggregation methods."""

    def test_events_below_1k_filtered_in_by_exchange(self):
        """Test events below $1K threshold are filtered in by_exchange()."""
        agg = OrderFlowAggregator()
        agg.add_event(make_trade_event(exchange="bybit", value_usd=500))
        agg.add_event(make_trade_event(exchange="bybit", value_usd=800))
        agg.add_event(make_trade_event(exchange="bybit", value_usd=1500))

        stats = agg.by_exchange()
        # Only event >= 1000 should be counted
        assert stats["bybit"]["count"] == 1

    def test_events_below_1k_filtered_in_totals(self):
        """Test events below $1K threshold are filtered in totals()."""
        agg = OrderFlowAggregator()
        agg.add_event(make_trade_event(side="buy", value_usd=500))
        agg.add_event(make_trade_event(side="buy", value_usd=2000))

        buy_usd, sell_usd, delta = agg.totals()
        # Only the $2000 trade should count
        assert buy_usd == 2000
