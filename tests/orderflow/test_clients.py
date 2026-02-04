# tests/orderflow/test_clients.py
from src.orderflow.clients import BybitTradeClient, BinanceTradeClient


class TestBybitTradeClient:
    """Tests for BybitTradeClient."""

    def test_bybit_client_attributes(self):
        """Test BybitTradeClient has correct attributes."""
        client = BybitTradeClient(coins=["BTC", "ETH"], on_event=lambda e: None)
        assert client.exchange_name == "bybit"
        assert "bybit" in client.ws_url

    def test_bybit_parse_buy_message(self):
        """Test parsing a buy trade message from Bybit."""
        client = BybitTradeClient(coins=["BTC"], on_event=lambda e: None)

        # Bybit trade message format
        data = {
            "topic": "publicTrade.BTCUSDT",
            "type": "snapshot",
            "ts": 1700000000000,
            "data": [
                {
                    "T": 1700000000000,
                    "s": "BTCUSDT",
                    "S": "Buy",  # Buy side
                    "v": "0.5",
                    "p": "90000.00",
                    "i": "trade_id_123",
                }
            ],
        }

        event = client.parse_message(data)
        assert event is not None
        assert event.exchange == "bybit"
        assert event.coin == "BTC"
        assert event.side == "buy"
        assert event.price == 90000.0
        assert event.size == 0.5
        assert event.value_usd == 45000.0

    def test_bybit_parse_sell_message(self):
        """Test parsing a sell trade message from Bybit."""
        client = BybitTradeClient(coins=["ETH"], on_event=lambda e: None)

        data = {
            "topic": "publicTrade.ETHUSDT",
            "type": "snapshot",
            "data": [
                {
                    "T": 1700000000000,
                    "s": "ETHUSDT",
                    "S": "Sell",  # Sell side
                    "v": "10.0",
                    "p": "3400.00",
                }
            ],
        }

        event = client.parse_message(data)
        assert event is not None
        assert event.side == "sell"

    def test_bybit_parse_irrelevant_message(self):
        """Test parsing an irrelevant message returns None."""
        client = BybitTradeClient(coins=["BTC"], on_event=lambda e: None)
        event = client.parse_message({"op": "subscribe", "success": True})
        assert event is None

    def test_bybit_trades_below_1k_return_none(self):
        """Test trades below $1K threshold return None."""
        client = BybitTradeClient(coins=["BTC"], on_event=lambda e: None)

        data = {
            "topic": "publicTrade.BTCUSDT",
            "type": "snapshot",
            "data": [
                {
                    "T": 1700000000000,
                    "s": "BTCUSDT",
                    "S": "Buy",
                    "v": "0.01",  # Small size
                    "p": "90000.00",  # = $900 value
                }
            ],
        }

        event = client.parse_message(data)
        assert event is None

    def test_bybit_size_classification_whale(self):
        """Test whale size classification in parsed events."""
        client = BybitTradeClient(coins=["BTC"], on_event=lambda e: None)

        data = {
            "topic": "publicTrade.BTCUSDT",
            "type": "snapshot",
            "data": [
                {
                    "T": 1700000000000,
                    "s": "BTCUSDT",
                    "S": "Buy",
                    "v": "20.0",  # $1.8M value
                    "p": "90000.00",
                }
            ],
        }

        event = client.parse_message(data)
        assert event is not None
        assert event.size_category == "whale"

    def test_bybit_size_classification_large(self):
        """Test large size classification."""
        client = BybitTradeClient(coins=["BTC"], on_event=lambda e: None)

        data = {
            "topic": "publicTrade.BTCUSDT",
            "type": "snapshot",
            "data": [
                {
                    "T": 1700000000000,
                    "s": "BTCUSDT",
                    "S": "Buy",
                    "v": "5.0",  # $450K value
                    "p": "90000.00",
                }
            ],
        }

        event = client.parse_message(data)
        assert event is not None
        assert event.size_category == "large"

    def test_bybit_size_classification_medium(self):
        """Test medium size classification."""
        client = BybitTradeClient(coins=["BTC"], on_event=lambda e: None)

        data = {
            "topic": "publicTrade.BTCUSDT",
            "type": "snapshot",
            "data": [
                {
                    "T": 1700000000000,
                    "s": "BTCUSDT",
                    "S": "Buy",
                    "v": "0.5",  # $45K value
                    "p": "90000.00",
                }
            ],
        }

        event = client.parse_message(data)
        assert event is not None
        assert event.size_category == "medium"

    def test_bybit_size_classification_small(self):
        """Test small size classification."""
        client = BybitTradeClient(coins=["BTC"], on_event=lambda e: None)

        data = {
            "topic": "publicTrade.BTCUSDT",
            "type": "snapshot",
            "data": [
                {
                    "T": 1700000000000,
                    "s": "BTCUSDT",
                    "S": "Buy",
                    "v": "0.05",  # $4.5K value
                    "p": "90000.00",
                }
            ],
        }

        event = client.parse_message(data)
        assert event is not None
        assert event.size_category == "small"


class TestBinanceTradeClient:
    """Tests for BinanceTradeClient."""

    def test_binance_client_attributes(self):
        """Test BinanceTradeClient has correct attributes."""
        client = BinanceTradeClient(coins=["BTC", "ETH"], on_event=lambda e: None)
        assert client.exchange_name == "binance"
        assert "binance" in client.ws_url

    def test_binance_parse_buy_message(self):
        """Test parsing a buy trade (m=false) from Binance."""
        client = BinanceTradeClient(coins=["BTC"], on_event=lambda e: None)

        # Binance aggTrade message format (raw, no wrapper with /ws/ URL)
        # m=false means the buyer is the maker, trade is a BUY
        data = {
            "e": "aggTrade",
            "E": 1700000000000,
            "s": "BTCUSDT",
            "a": 12345,  # Aggregate trade ID
            "p": "90000.00",  # Price
            "q": "0.5",  # Quantity
            "f": 100,  # First trade ID
            "l": 100,  # Last trade ID
            "T": 1700000000000,  # Trade time
            "m": False,  # m=false -> buyer is maker -> BUY
        }

        event = client.parse_message(data)
        assert event is not None
        assert event.exchange == "binance"
        assert event.coin == "BTC"
        assert event.side == "buy"
        assert event.price == 90000.0
        assert event.size == 0.5
        assert event.value_usd == 45000.0

    def test_binance_parse_sell_message(self):
        """Test parsing a sell trade (m=true) from Binance."""
        client = BinanceTradeClient(coins=["ETH"], on_event=lambda e: None)

        # m=true means the seller is the maker, trade is a SELL
        data = {
            "e": "aggTrade",
            "E": 1700000000000,
            "s": "ETHUSDT",
            "p": "3400.00",
            "q": "10.0",
            "T": 1700000000000,
            "m": True,  # m=true -> seller is maker -> SELL
        }

        event = client.parse_message(data)
        assert event is not None
        assert event.side == "sell"

    def test_binance_m_true_is_sell(self):
        """Test Binance m=true -> sell explicitly."""
        client = BinanceTradeClient(coins=["BTC"], on_event=lambda e: None)

        data = {
            "e": "aggTrade",
            "s": "BTCUSDT",
            "p": "90000.00",
            "q": "1.0",
            "T": 1700000000000,
            "m": True,
        }

        event = client.parse_message(data)
        assert event is not None
        assert event.side == "sell"

    def test_binance_m_false_is_buy(self):
        """Test Binance m=false -> buy explicitly."""
        client = BinanceTradeClient(coins=["BTC"], on_event=lambda e: None)

        data = {
            "e": "aggTrade",
            "s": "BTCUSDT",
            "p": "90000.00",
            "q": "1.0",
            "T": 1700000000000,
            "m": False,
        }

        event = client.parse_message(data)
        assert event is not None
        assert event.side == "buy"

    def test_binance_parse_irrelevant_message(self):
        """Test parsing an irrelevant message returns None."""
        client = BinanceTradeClient(coins=["BTC"], on_event=lambda e: None)
        event = client.parse_message({"result": None, "id": 1})
        assert event is None

    def test_binance_trades_below_1k_return_none(self):
        """Test trades below $1K threshold return None."""
        client = BinanceTradeClient(coins=["BTC"], on_event=lambda e: None)

        data = {
            "e": "aggTrade",
            "s": "BTCUSDT",
            "p": "90000.00",
            "q": "0.01",  # = $900 value
            "T": 1700000000000,
            "m": False,
        }

        event = client.parse_message(data)
        assert event is None

    def test_binance_size_classification_whale(self):
        """Test whale size classification in parsed events."""
        client = BinanceTradeClient(coins=["BTC"], on_event=lambda e: None)

        data = {
            "e": "aggTrade",
            "s": "BTCUSDT",
            "p": "90000.00",
            "q": "20.0",  # $1.8M value
            "T": 1700000000000,
            "m": False,
        }

        event = client.parse_message(data)
        assert event is not None
        assert event.size_category == "whale"

    def test_binance_size_classification_large(self):
        """Test large size classification."""
        client = BinanceTradeClient(coins=["BTC"], on_event=lambda e: None)

        data = {
            "e": "aggTrade",
            "s": "BTCUSDT",
            "p": "90000.00",
            "q": "5.0",  # $450K value
            "T": 1700000000000,
            "m": False,
        }

        event = client.parse_message(data)
        assert event is not None
        assert event.size_category == "large"

    def test_binance_size_classification_medium(self):
        """Test medium size classification."""
        client = BinanceTradeClient(coins=["BTC"], on_event=lambda e: None)

        data = {
            "e": "aggTrade",
            "s": "BTCUSDT",
            "p": "90000.00",
            "q": "0.5",  # $45K value
            "T": 1700000000000,
            "m": False,
        }

        event = client.parse_message(data)
        assert event is not None
        assert event.size_category == "medium"

    def test_binance_size_classification_small(self):
        """Test small size classification."""
        client = BinanceTradeClient(coins=["BTC"], on_event=lambda e: None)

        data = {
            "e": "aggTrade",
            "s": "BTCUSDT",
            "p": "90000.00",
            "q": "0.05",  # $4.5K value
            "T": 1700000000000,
            "m": False,
        }

        event = client.parse_message(data)
        assert event is not None
        assert event.size_category == "small"
