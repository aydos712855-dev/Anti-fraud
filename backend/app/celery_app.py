"""Celery tasks for async ML, scoring, and data ingestion."""
from celery import Celery
from celery.schedules import crontab

from app.config import get_settings

settings = get_settings()

celery = Celery(
    "antifrod",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.scoring", "app.tasks.ingestion", "app.tasks.ml_training"],
)

celery.conf.beat_schedule = {
    # Ingest trades from Match-Trader every minute
    "ingest-trades": {
        "task": "app.tasks.ingestion.ingest_trades",
        "schedule": 60.0,
    },
    # Recompute risk scores for accounts with new trades every 5 min
    "recompute-scores": {
        "task": "app.tasks.scoring.recompute_dirty_scores",
        "schedule": 300.0,
    },
    # Retrain ML models daily at 03:00 UTC
    "retrain-ml": {
        "task": "app.tasks.ml_training.retrain_all_models",
        "schedule": crontab(hour=3, minute=0),
    },
    # Sync economic calendar daily at 00:05 UTC
    "sync-calendar": {
        "task": "app.tasks.ingestion.sync_economic_calendar",
        "schedule": crontab(hour=0, minute=5),
    },
}


@celery.task(name="app.celery_app.run_rule_backtest")
def run_rule_backtest(rule_id: int, days: int = 30):
    """Backtest a rule against historical data."""
    import asyncio
    from app.db import AsyncSessionLocal
    from app.models.database import Rule, Account, Alert
    from sqlalchemy import select
    from datetime import datetime, timedelta, timezone

    async def _run():
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Rule).where(Rule.id == rule_id))
            rule = result.scalar_one_or_none()
            if not rule:
                return {"error": "Rule not found"}

            result2 = await db.execute(select(Account))
            accounts = result2.scalars().all()

            from app.modules.rules.engine import RulesEngine
            engine = RulesEngine(db)

            flagged = []
            for account in accounts:
                handler = engine._get_handler(rule.violation_type)
                if handler:
                    alerts = await handler(account.id, rule)
                    if alerts:
                        flagged.append(account.id)

            return {
                "rule_id": rule_id,
                "accounts_scanned": len(accounts),
                "accounts_flagged": len(flagged),
                "flagged_ids": flagged[:100],
            }

    return asyncio.run(_run())
