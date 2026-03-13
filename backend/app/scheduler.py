import asyncio
from datetime import datetime
import logging
import os
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)
DEFAULT_TZ = pytz.timezone("America/Sao_Paulo")
scheduler = AsyncIOScheduler(timezone=DEFAULT_TZ)

current_interval_min: int = int(os.getenv("HEARTBEAT_INTERVAL_MIN", "30"))


async def _run_heartbeat():
    from app.tools.market import get_market_data
    from app.tools.news import get_forex_news
    import app.agents.registry as registry

    logger.info("🔔 Heartbeat iniciado")
    try:
        from app.ws_manager import ws_manager
        await ws_manager.broadcast({"type": "heartbeat_start", "ts": datetime.now(DEFAULT_TZ).isoformat()})

        market_data = await get_market_data()
        news = await get_forex_news()
        lux = registry.get_agent("lux")
        report = await lux.run_heartbeat(market_data, news)
        
        await ws_manager.broadcast({
            "type": "heartbeat_completed",
            "decision": report.decision,
            "asset": report.asset,
            "ts": datetime.now(DEFAULT_TZ).isoformat()
        })
        logger.info(f"✅ Heartbeat concluído — Decisão: {report.decision} | Ativo: {report.asset}")
    except Exception as e:
        logger.error(f"❌ Heartbeat falhou: {e}", exc_info=True)
        from app.tools.telegram import send_alert
        await send_alert("Falha no Heartbeat", f"Erro crítico na execução do ciclo de análise: {str(e)}", level="ERROR")


def start_scheduler():
    global current_interval_min
    scheduler.add_job(
        _run_heartbeat,
        trigger=IntervalTrigger(minutes=current_interval_min),
        id="heartbeat",
        replace_existing=True,
        misfire_grace_time=60,
    )
    scheduler.start()


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
