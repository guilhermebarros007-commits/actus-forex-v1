import os
import MetaTrader5 as mt5
import threading

class MT5Client:
    _instance = None
    _lock = threading.Lock()
    _is_initialized = False

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(MT5Client, cls).__new__(cls)
            return cls._instance

    def connect(self):
        with self._lock:
            if self._is_initialized: return True
            if not mt5.initialize(): return False
            login = int(os.getenv("MT5_LOGIN", 0))
            password = os.getenv("MT5_PASSWORD", "")
            server = os.getenv("MT5_SERVER", "")
            if mt5.login(login, password=password, server=server):
                self._is_initialized = True
                return True
            return False

    def get_symbol_price(self, symbol):
        if not self.connect(): return None
        symbol = self._normalize_symbol(symbol)
        tick = mt5.symbol_info_tick(symbol)
        return {"bid": tick.bid, "ask": tick.ask} if tick else None

    def _normalize_symbol(self, symbol):
        if symbol and not symbol.endswith(".a"): return f"{symbol}.a"
        return symbol

    def place_order(self, symbol, order_type, volume, price=None, sl=None, tp=None):
        if not self.connect(): return None
        symbol = self._normalize_symbol(symbol)
        info = mt5.symbol_info(symbol)
        digits = info.digits if info else 5
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(volume),
            "type": mt5.ORDER_TYPE_BUY if order_type == "buy" else mt5.ORDER_TYPE_SELL,
            "magic": 123456,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        if price: request["price"] = round(float(price), digits)
        if sl: request["sl"] = round(float(sl), digits)
        if tp: request["tp"] = round(float(tp), digits)
        return mt5.order_send(request)
