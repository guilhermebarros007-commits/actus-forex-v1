import os
import asyncio
from datetime import datetime, timedelta
from app.tools.forex_mt5 import MT5Client

_cache: dict = {"data": None, "expires": datetime.min}
CACHE_TTL = timedelta(minutes=2)

async def get_market_data() -> dict:
    global _cache
    if _cache["data"] and datetime.utcnow() < _cache["expires"]:
        return _cache["data"]

    client = MT5Client()
    try:
        if not client.connect(): raise Exception("MT5 Connect Fail")
        data = {
            "eurusd": client.get_symbol_price("EURUSD"),
            "gbpusd": client.get_symbol_price("GBPUSD"),
            "usdjpy": client.get_symbol_price("USDJPY"),
            "audusd": client.get_symbol_price("AUDUSD"),
            "account": client.get_account_summary(),
            "fetched_at": datetime.utcnow().isoformat(),
        }
        _cache["data"] = data
        _cache["expires"] = datetime.utcnow() + CACHE_TTL
        return data
    except Exception as e:
        return _cache["data"] if _cache["data"] else {"error": str(e)}

def format_market_summary(data: dict) -> str:
    if "error" in data: return f"Erro: {data['error']}"
    lines = [f"{p.upper()}: Bid {data[p]['bid']} | Ask {data[p]['ask']}" for p in ["eurusd", "gbpusd", "usdjpy", "audusd"] if data.get(p)]
    acc = data.get("account")
    if acc and isinstance(acc, dict):
        lines.append(f"Balance: {acc['balance']} {acc['currency']} | Equity: {acc['equity']}")
    return "\n".join(lines)
