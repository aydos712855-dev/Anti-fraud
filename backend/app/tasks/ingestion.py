"""Celery tasks: data ingestion from Match-Trader."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


def ingest_trades():
    """Pull new trades from Match-Trader API and persist them."""
    async def _run():
        from app.db import AsyncSessionLocal
        # TODO: implement Match-Trader API client
        # For now: placeholder that logs the intent
        logger.info("Trade ingestion tick at %s", datetime.now(timezone.utc))
        # 1. GET /api/trades?since=<last_ingested>
        # 2. Normalize to Trade model
        # 3. Upsert into DB
        # 4. For each new trade, enqueue score recompute

    asyncio.run(_run())


def sync_economic_calendar():
    """Pull today's high-impact events from economic calendar API."""
    async def _run():
        from app.db import AsyncSessionLocal
        logger.info("Economic calendar sync at %s", datetime.now(timezone.utc))
        # TODO: implement calendar API client and store events in Redis/DB

    asyncio.run(_run())
