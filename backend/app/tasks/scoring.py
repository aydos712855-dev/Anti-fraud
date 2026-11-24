"""Celery tasks: risk score recompute."""
import asyncio
import logging

logger = logging.getLogger(__name__)


def recompute_dirty_scores():
    """Find accounts with new trades since last score and recompute."""
    async def _run():
        from app.db import AsyncSessionLocal
        from app.models.database import Account, Trade, RiskScore
        from app.modules.scoring.calculator import RiskScoreCalculator
        from sqlalchemy import select, func
        from datetime import datetime, timedelta, timezone

        async with AsyncSessionLocal() as db:
            since = datetime.now(timezone.utc) - timedelta(minutes=10)
            result = await db.execute(
                select(Trade.account_id).where(Trade.open_time >= since).distinct()
            )
            dirty_ids = [r[0] for r in result.all()]

            calc = RiskScoreCalculator(db)
            for acc_id in dirty_ids:
                try:
                    await calc.compute_account_score(acc_id, trigger="trade")
                except Exception:
                    logger.exception("Failed to recompute score for account %s", acc_id)

            await db.commit()
            logger.info("Recomputed scores for %d accounts", len(dirty_ids))

    asyncio.run(_run())
