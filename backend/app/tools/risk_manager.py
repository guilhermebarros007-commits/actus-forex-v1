import logging
import pandas as pd
from app.tools.forex_mt5 import MT5Client

logger = logging.getLogger(__name__)

class RiskManager:
    def __init__(self, risk_per_trade: float = 0.01):
        self.risk_per_trade = risk_per_trade
        self.mt5 = MT5Client()

    def calculate_atr(self, symbol: str, period: int = 14) -> float:
        try:
            self.mt5.connect()
            import MetaTrader5 as mt5_lib
            rates = mt5_lib.copy_rates_from_pos(self.mt5._normalize_symbol(symbol), mt5_lib.TIMEFRAME_M1, 0, period + 1)
            if rates is None: return 0.05
            df = pd.DataFrame(rates)
            df['tr'] = df['high'] - df['low']
            return float(df['tr'].tail(period).mean())
        except Exception:
            return 0.0020

    def calculate_position_size(self, symbol: str, entry_price: float, sl_price: float) -> float:
        acc = self.mt5.get_account_info()
        if not acc: return 0.01
        risk_amount = acc.get("equity", 0) * self.risk_per_trade
        price_risk = abs(entry_price - sl_price)
        if price_risk == 0: return 0.01
        import MetaTrader5 as mt5_lib
        info = mt5_lib.symbol_info(self.mt5._normalize_symbol(symbol))
        multiplier = 1.0 / info.point if info else 100000
        volume = risk_amount / (price_risk * multiplier)
        return max(0.01, round(volume, 2))
