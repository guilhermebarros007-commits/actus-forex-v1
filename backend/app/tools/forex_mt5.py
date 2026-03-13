import os
import MetaTrader5 as mt5
from dotenv import load_dotenv
import threading

load_dotenv()

class MT5Client:
    _instance = None
    _lock = threading.Lock()
    _is_initialized = False

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(MT5Client, cls).__new__(cls)
            return cls._instance

    def __init__(self):
        # Only run init once
        if hasattr(self, 'login'): return
        
        self.login = int(os.getenv("MT5_LOGIN", 0))
        self.server = os.getenv("MT5_SERVER", "")
        self.password = os.getenv("MT5_PASSWORD", "")

    def connect(self):
        """Initializes and logs into the MT5 terminal once."""
        with self._lock:
            if self._is_initialized:
                return True

            if not mt5.initialize():
                print(f"initialize() failed, error code = {mt5.last_error()}")
                return False

            authorized = mt5.login(self.login, password=self.password, server=self.server)
            if authorized:
                print(f"Connected to MT5 account {self.login}")
                self._is_initialized = True
                return True
            else:
                print(f"Failed to connect to MT5 account {self.login}, error code = {mt5.last_error()}")
                return False

    def get_account_info(self):
        """Returns account information."""
        if not self.connect(): return None
        account_info = mt5.account_info()
        return account_info._asdict() if account_info else None

    def get_market_data(self, symbol, count=100, timeframe=mt5.TIMEFRAME_M1):
        """Retrieves historical data for a symbol."""
        if not self.connect(): return None
        symbol = self._normalize_symbol(symbol)
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
        return rates

    def get_symbol_price(self, symbol):
        """Returns the current bid/ask for a symbol."""
        if not self.connect(): return None
        symbol = self._normalize_symbol(symbol)
        tick = mt5.symbol_info_tick(symbol)
        if tick is None: return None
        return {"bid": tick.bid, "ask": tick.ask, "last": tick.last}

    def get_symbol_info(self, symbol):
        """Returns full symbol information."""
        if not self.connect(): return None
        symbol = self._normalize_symbol(symbol)
        return mt5.symbol_info(symbol)

    def _normalize_symbol(self, symbol):
        """Adds .a suffix if missing (Pepperstone specific)."""
        if symbol and not symbol.endswith(".a"):
            return f"{symbol}.a"
        return symbol

    def get_account_summary(self):
        """Returns a simplified account summary."""
        info = self.get_account_info()
        if not info: return "Indisponível"
        return {
            "login": info.get("login"),
            "balance": info.get("balance"),
            "equity": info.get("equity"),
            "margin": info.get("margin"),
            "margin_free": info.get("margin_free"),
            "currency": info.get("currency")
        }

    def list_positions(self):
        """Returns active positions."""
        if not self.connect(): return []
        positions = mt5.positions_get()
        return [p._asdict() for p in positions] if positions else []

    def place_order(self, symbol, order_type, volume, price=None, sl=None, tp=None):
        """Places a trade order."""
        if not self.connect(): return None
        symbol = self._normalize_symbol(symbol)
        mt5.symbol_select(symbol, True)
        
        # Get symbol digits for rounding
        info = self.get_symbol_info(symbol)
        digits = info.digits if info else 5
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(volume),
            "type": order_type,
            "magic": 123456,
            "comment": "ACTUS-FOREX Python AutoTrade",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        if price: request["price"] = round(float(price), digits)
        if sl: request["sl"] = round(float(sl), digits)
        if tp: request["tp"] = round(float(tp), digits)

        return mt5.order_send(request)

    def get_open_positions(self):
        """Returns all open positions as dictionaries."""
        return self.list_positions()

    def modify_order_sl_tp(self, ticket, sl, tp):
        """Modifies SL/TP for an existing position."""
        if not self.connect(): return False
        position = mt5.positions_get(ticket=ticket)
        if not position: return False
            
        symbol = position[0].symbol
        info = self.get_symbol_info(symbol)
        digits = info.digits if info else 5

        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": symbol,
            "sl": round(float(sl), digits),
            "tp": round(float(tp), digits),
            "position": ticket
        }
        return mt5.order_send(request)

    def get_market_depth(self, symbol):
        """Retrieves Market Depth (DOM) for a symbol."""
        if not self.connect(): return None
        symbol = self._normalize_symbol(symbol)
        mt5.market_book_add(symbol)
        items = mt5.market_book_get(symbol)
        return [i._asdict() for i in items] if items else None

    def disconnect(self):
        """
        No-op in singleton pattern to avoid killing shared connections.
        Use force_shutdown if you really need to kill the terminal.
        """
        pass

    def force_shutdown(self):
        """Shuts down the connection to MT5 for real."""
        with self._lock:
            mt5.shutdown()
            self._is_initialized = False
