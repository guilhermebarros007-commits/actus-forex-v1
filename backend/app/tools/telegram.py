import os
import asyncio
import logging
from telethon import TelegramClient
from app.tools.market import get_market_data, format_market_summary

logger = logging.getLogger(__name__)

async def send_daily_report():
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not all([api_id, api_hash, bot_token, chat_id]):
        logger.warning("Telegram credentials missing.")
        return

    try:
        market_data = await get_market_data()
        summary = format_market_summary(market_data)
        
        message = f"📊 *ACTUS-FOREX Relatório Diário*\n\n{summary}\n\nStatus: Operacional ✅"
        
        async with TelegramClient('bot_session', api_id, api_hash) as client:
            await client.start(bot_token=bot_token)
            await client.send_message(int(chat_id), message, parse_mode='markdown')
            
        logger.info("Daily report sent to Telegram.")
    except Exception as e:
        logger.error(f"Error sending Telegram report: {e}")
