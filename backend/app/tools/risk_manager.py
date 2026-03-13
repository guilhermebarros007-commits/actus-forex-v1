import logging
import pandas as pd
import numpy as np
from app.tools.forex_mt5 import MT5Client

logger = logging.getLogger(__name__)

class RiskManager:
    def __init__(self, risk_per_trade: float = 0.01, max_drawdown: float = 0.05, max_spread_pct: float = 0.0005):
        self.risk_per_trade = risk_per_trade
        self.max_drawdown = max_drawdown
        self.max_spread_pct = max_spread_pct
        self.mt5 = MT5Client()

    def calculate_atr(self, symbol: str, period: int = 14) -> float:
        try:
            self.mt5.connect()
            rates = self.mt5.get_market_data(symbol, count=period + 1)
            if rates is None or len(rates) < period:
                return 0.0
            df = pd.DataFrame(rates)
            df['prev_close'] = df['close'].shift(1)
            df['tr1'] = df['high'] - df['low']
            df['tr2'] = abs(df['high'] - df['prev_close'])
            df['tr3'] = abs(df['low'] - df['prev_close'])
            df['tr'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
            atr = df['tr'].tail(period).mean()
            return float(atr)
        except Exception as e:
            logger.error(f"Erro ao calcular ATR para {symbol}: {e}")
            return 0.0

    def calculate_position_size(self, symbol: str, entry_price: float, sl_price: float) -> float:
        try:
            acc_info = self.mt5.get_account_info()
            if not acc_info: return 0.01
            equity = acc_info.get("equity", 0)
            risk_amount = equity * self.risk_per_trade
            price_risk = abs(entry_price - sl_price)
            if price_risk == 0: return 0.01
            symbol_info = self.mt5.get_symbol_info(symbol)
            multiplier = 1.0 / symbol_info.point if symbol_info and symbol_info.point > 0 else 100000
            volume = risk_amount / (price_risk * multiplier)
            volume = max(0.01, round(volume, 2))
            if equity < 1000: volume = min(volume, 0.10)
            return float(volume)
        except Exception as e:
            logger.error(f"Erro no cálculo de position size: {e}")
            return 0.01

    def get_risk_params(self, symbol: str, direction: str, current_price: float):
        atr = self.calculate_atr(symbol) or 0.0020
        sl_dist = atr * 1.5
        tp_dist = atr * 3.0
        if direction == "long":
            sl = current_price - sl_dist
            tp = current_price + tp_dist
        else:
            sl = current_price + sl_dist
            tp = current_price - tp_dist
        volume = self.calculate_position_size(symbol, current_price, sl)
        return {"volume": volume, "sl": float(sl), "tp": float(tp), "atr": float(atr)}

    def check_drawdown_limit(self) -> bool:
        acc = self.mt5.get_account_info()
        if not acc: return False
        balance, equity = acc.get("balance", 0), acc.get("equity", 0)
        if balance > 0 and (balance - equity) / balance > self.max_drawdown:
            logger.warning("DRAWDOWN CRÍTICO.")
            return True
        return False

    def validate_execution_guard(self, symbol: str) -> bool:
        tick = self.mt5.get_symbol_price(symbol)
        if not tick: return False
        spread_pct = (tick["ask"] - tick["bid"]) / tick["bid"]
        return spread_pct <= self.max_spread_pct

    def apply_dynamic_profit_management(self):
        positions = self.mt5.get_open_positions()
        for pos in positions:
            symbol, ticket, price_open, price_current, sl_current = pos['symbol'], pos['ticket'], pos['price_open'], pos['price_current'], pos['sl']
            atr = self.calculate_atr(symbol)
            if atr == 0: continue
            trigger_dist = atr * 1.5
            if pos['type'] == 0: # BUY
                if (price_current - price_open) > trigger_dist and sl_current < price_open:
                    self.mt5.modify_order_sl_tp(ticket, price_open, pos['tp'])
            else: # SELL
                if (price_open - price_current) > trigger_dist and (sl_current > price_open or sl_current == 0):
                    self.mt5.modify_order_sl_tp(ticket, price_open, pos['tp'])
