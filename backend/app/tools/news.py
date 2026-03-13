import logging
import feedparser
import asyncio
from datetime import datetime
from typing import List

logger = logging.getLogger(__name__)

RSS_FEEDS = [
    "https://www.forexlive.com/feed",
    "https://www.dailyfx.com/feeds/forex-news",
    "https://www.investing.com/rss/news_1.rss"
]

async def get_forex_news() -> List[dict]:
    news_items = []
    loop = asyncio.get_event_loop()
    async def fetch_feed(url):
        try:
            feed = await loop.run_in_executor(None, feedparser.parse, url)
            for entry in feed.entries[:5]:
                news_items.append({
                    "title": entry.get("title", "No Title"),
                    "source": feed.get("feed", {}).get("title", "Forex Source"),
                    "url": entry.get("link", ""),
                    "published_at": entry.get("published", datetime.utcnow().isoformat()),
                })
        except Exception as e:
            logger.error(f"Feed error {url}: {e}")
    await asyncio.gather(*(fetch_feed(url) for url in RSS_FEEDS))
    return news_items if news_items else [{"title": "Syncing...", "source": "System", "url": "#"}]

def format_news_summary(news: List[dict]) -> str:
    return "\n".join([f"[{n.get('source')}] {n.get('title')}" for n in news[:10]])
