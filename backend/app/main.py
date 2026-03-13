import json
import logging
import os
from collections import deque
from collections.abc import AsyncIterator
import asyncio
import datetime
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.ws_manager import ws_manager

from app.memory import db as memory_db
from app.scheduler import start_scheduler, stop_scheduler, reschedule_heartbeat, current_interval_min
from app.tools.market import get_market_data
from app.tools.news import get_forex_news
from app.tools.forex_mt5 import MT5Client

# Reuse MT5 functions where possible or define mapping
async def get_mt5_technical_data():
    client = MT5Client()
    if not client.connect():
        return {"error": "MT5 not connected"}
    
    # Simple Technical Analysis for UI
    pairs = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD"]
    results = {}
    for p in pairs:
        # This is a simplified version for the UI
        # In a real app, this would use pandas-ta on OHLC data
        results[p] = {
            "rsi_14": 55.4,
            "trend_ema": "BULL",
            "macd_trend": "BULL",
            "last_close": 1.0850 if p == "EURUSD" else 150.20,
            "bb_upper": 1.0900,
            "bb_lower": 1.0800,
            "ema9": 1.0845,
            "atr_14": 0.0050
        }
    client.disconnect()
    return results

async def get_mt5_portfolio():
    client = MT5Client()
    if not client.connect():
        return [{"agent_id": "lux", "error": "MT5 not connected"}]
    
    acc = client.get_account_summary()
    positions = client.list_positions()
    
    # Map back to agent structure
    # For now, let's assume one main account for simplicity
    portfolio = [{
        "agent_id": "lux",
        "account_value": acc.get("equity", 0),
        "total_value": acc.get("equity", 0),
        "balance": acc.get("balance", 0),
        "total_pnl": acc.get("pnl", 0),
        "total_margin_used": acc.get("margin", 0),
        "withdrawable": acc.get("margin_free", 0),
        "currency": acc.get("currency", "USD"),
        "positions": positions
    }]
    client.disconnect()
    return portfolio

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent.parent / "static"
VALID_AGENTS = ["lux", "hype_beast", "oracle", "vitalik"]
TRADER_AGENTS = ["hype_beast", "oracle", "vitalik"]

# ── Logging System ────────────────────────────────────────────────────────────
LOG_QUEUES = []
LOG_BUFFER = deque(maxlen=100)

class LogStreamHandler(logging.Handler):
    def emit(self, record):
        try:
            log_entry = {
                "ts": datetime.datetime.now().strftime("%H:%M:%S"),
                "tag": record.name.split(".")[-1].upper(),
                "msg": self.format(record),
                "level": record.levelname
            }
            # Add to buffer
            LOG_BUFFER.append(log_entry)
            # Broadcast to all active queues
            for q in LOG_QUEUES:
                asyncio.run_coroutine_threadsafe(q.put(log_entry), asyncio.get_event_loop())
        except Exception:
            pass

# Setup logging to capture app logs
stream_handler = LogStreamHandler()
stream_handler.setFormatter(logging.Formatter("%(message)s"))
logging.getLogger("app").addHandler(stream_handler)
logging.getLogger("app.agents").addHandler(stream_handler)
logging.getLogger("app.scheduler").addHandler(stream_handler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await memory_db.init_db()
    logger.info("📦 Banco de dados inicializado")

    # Register agents
    import app.agents.registry as registry
    from app.agents.lux import LuxAgent
    from app.agents.traders import HypeBeastAgent, OracleAgent, VitalikAgent

    registry.agents["lux"] = LuxAgent()
    registry.agents["hype_beast"] = HypeBeastAgent()
    registry.agents["oracle"] = OracleAgent()
    registry.agents["vitalik"] = VitalikAgent()
    logger.info("🤖 Agentes registrados: " + ", ".join(registry.agents.keys()))

    start_scheduler()
    yield

    # Shutdown
    stop_scheduler()


app = FastAPI(title="Trading Agents", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ── Root ──────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            # Keep connection alive and receive messages if needed
            data = await websocket.receive_text()
            # Echo or handle incoming data if necessary
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        ws_manager.disconnect(websocket)


# ── Market & News ─────────────────────────────────────────────────────────────

@app.get("/api/market")
async def api_market():
    return await get_market_data()


@app.get("/api/news")
async def api_news():
    return await get_forex_news()


# ── Heartbeat ─────────────────────────────────────────────────────────────────

@app.get("/api/heartbeat/history")
async def heartbeat_history(limit: int = 20):
    return await memory_db.get_reports(limit=limit)


@app.post("/api/heartbeat/trigger")
async def heartbeat_trigger():
    import app.agents.registry as registry

    market_data = await get_market_data()
    news = await get_forex_news()
    lux = registry.get_agent("lux")
    report = await lux.run_heartbeat(market_data, news)
    return report.to_dict()


class HeartbeatSettings(BaseModel):
    interval_min: int


@app.post("/api/settings/heartbeat")
async def update_heartbeat_interval(req: HeartbeatSettings):
    """Dynamically change heartbeat interval (1–120 min)."""
    if not (1 <= req.interval_min <= 120):
        raise HTTPException(status_code=400, detail="interval_min must be between 1 and 120")
    reschedule_heartbeat(req.interval_min)
    return {"interval_min": req.interval_min, "status": "rescheduled"}


@app.get("/api/settings/heartbeat")
async def get_heartbeat_interval():
    import app.scheduler as sched
    return {"interval_min": sched.current_interval_min}


@app.get("/api/scheduler/next_run")
async def get_next_run():
    """Returns the ISO timestamp of the next scheduled heartbeat."""
    from app.scheduler import scheduler
    job = scheduler.get_job("heartbeat")
    if not job or not job.next_run_time:
        return {"next_run": None}
    return {"next_run": job.next_run_time.isoformat()}


@app.post("/api/telegram/test")
async def telegram_test():
    """Send a test message to Telegram."""
    from app.tools.telegram import send_message
    ok = await send_message("🤖 <b>Trading Agents</b> — conexão Telegram OK!")
    return {"sent": ok}


@app.post("/api/telegram/report")
async def telegram_report():
    """Trigger the daily Telegram report manually."""
    from app.tools.telegram import send_daily_report
    ok = await send_daily_report()
    return {"sent": ok}


# ── Chat ──────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str


@app.post("/api/chat/{agent_id}/stream")
async def chat_stream(agent_id: str, req: ChatRequest):
    """SSE streaming endpoint — yields text/event-stream chunks."""
    if agent_id not in VALID_AGENTS:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    import app.agents.registry as registry
    agent = registry.get_agent(agent_id)

    async def event_generator() -> AsyncIterator[str]:
        try:
            async for chunk in agent.stream_chat(req.message):
                yield f"data: {json.dumps({'text': chunk})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/chat/{agent_id}")
async def chat(agent_id: str, req: ChatRequest):
    """Non-streaming fallback."""
    if agent_id not in VALID_AGENTS:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    import app.agents.registry as registry
    agent = registry.get_agent(agent_id)
    reply = await agent.chat(req.message)
    return {"agent_id": agent_id, "reply": reply}


@app.get("/api/chat/{agent_id}/history")
async def chat_history(agent_id: str, limit: int = 20):
    if agent_id not in VALID_AGENTS:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    return await memory_db.get_chat_history(agent_id, limit=limit)


# ── Logs ──────────────────────────────────────────────────────────────────────

@app.get("/api/v1/logs/stream")
async def logs_stream():
    """SSE endpoint for real-time terminal logs."""
    async def event_generator():
        queue = asyncio.Queue()
        LOG_QUEUES.append(queue)
        try:
            # Send buffer first
            for entry in list(LOG_BUFFER):
                yield f"data: {json.dumps(entry)}\n\n"
            
            # Streaming
            while True:
                log_entry = await queue.get()
                yield f"data: {json.dumps(log_entry)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            LOG_QUEUES.remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Agent Memory ──────────────────────────────────────────────────────────────

@app.get("/api/agents/{agent_id}/memory")
async def agent_memory(agent_id: str):
    if agent_id not in VALID_AGENTS:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    import app.agents.registry as registry
    agent = registry.get_agent(agent_id)
    memory_path = agent.workspace / "MEMORY.md"
    content = memory_path.read_text(encoding="utf-8") if memory_path.exists() else ""
    return {"agent_id": agent_id, "memory": content}


# ── Trades History ────────────────────────────────────────────────────────────

@app.get("/api/trades")
async def trades_list(agent_id: str = None, limit: int = 50):
    """List trade execution history. Optionally filter by agent_id."""
    return await memory_db.get_trades(agent_id=agent_id, limit=limit)


# ── MT5: Market & Technical ──────────────────────────────────────────

@app.get("/api/mt5/market")
async def mt5_market():
    """Real-time prices from MT5."""
    return await get_market_data()


@app.get("/api/mt5/technical")
async def mt5_technical():
    """Technical data for major pairs."""
    return await get_mt5_technical_data()


@app.get("/api/mt5/candles")
async def mt5_candles(symbol: str = "EURUSD", interval: str = "1h", count: int = 100):
    """Raw OHLCV candle data from MT5."""
    client = MT5Client()
    if not client.connect():
        return {"error": "MT5 not connected"}
    
    tf_map = {
        "1m": mt5.TIMEFRAME_M1,
        "5m": mt5.TIMEFRAME_M5,
        "15m": mt5.TIMEFRAME_M15,
        "1h": mt5.TIMEFRAME_H1,
        "4h": mt5.TIMEFRAME_H4,
        "1d": mt5.TIMEFRAME_D1,
    }
    tf = tf_map.get(interval, mt5.TIMEFRAME_H1)
    
    rates = client.get_market_data(symbol, count=count, timeframe=tf)
    client.disconnect()
    
    if rates is None:
        return []
        
    # Format for Lightweight Charts: { time: unixtime, open: x, high: x, low: x, close: x }
    # Lightweight Charts expects time in seconds
    return [
        {
            "time": int(r['time']), # MT5 returns time in seconds
            "open": float(r['open']),
            "high": float(r['high']),
            "low": float(r['low']),
            "close": float(r['close']),
            "tick_volume": int(r['tick_volume'])
        }
        for r in rates
    ]


@app.get("/api/mt5/depth")
async def mt5_depth(symbol: str = "EURUSD"):
    """Get Market Depth (DOM) for a symbol."""
    client = MT5Client()
    if not client.connect():
        return {"error": "MT5 not connected"}
    
    depth = client.get_market_depth(symbol)
    client.disconnect()
    
    if depth is None:
        return {"error": f"Failed to get depth for {symbol}"}
        
    return depth


# ── MT5: Portfolio ────────────────────────────────────────────────────

@app.get("/api/mt5/portfolio")
async def mt5_portfolio():
    """Account state for all agents."""
    return await get_mt5_portfolio()


@app.get("/api/mt5/account/{agent_id}")
async def mt5_account(agent_id: str):
    """Account state for a specific agent."""
    p = await get_mt5_portfolio()
    for acc in p:
        if acc["agent_id"] == agent_id:
            return acc
    return {"error": "Not found"}


# ── MT5: Order Execution ──────────────────────────────────────────────

class MarketOrderRequest(BaseModel):
    symbol: str
    is_buy: bool
    size: float

class CloseRequest(BaseModel):
    symbol: str
    size: Optional[float] = None

@app.post("/api/mt5/order/{agent_id}/market")
async def mt5_market_order(agent_id: str, req: MarketOrderRequest):
    """Open a market position for an agent."""
    client = MT5Client()
    if not client.connect():
        raise HTTPException(status_code=500, detail="MT5 not connected")
    
    result = client.place_order(req.symbol, "buy" if req.is_buy else "sell", req.size)
    client.disconnect()
    
    if not result:
        raise HTTPException(status_code=400, detail="Order failed")
    
    return {"success": True, "order_id": result.order}


@app.post("/api/mt5/order/{agent_id}/close")
async def mt5_close_position(agent_id: str, req: CloseRequest):
    """Close a position for an agent."""
    # Simplified close-by-symbol for MT5 context
    client = MT5Client()
    if not client.connect():
        raise HTTPException(status_code=500, detail="MT5 not connected")
    
    # In MT5 we usually close by ticket or opposite order
    # For simplicity, we'll implement a helper in client if needed
    client.disconnect()
    return {"success": False, "error": "Not implemented yet"}
