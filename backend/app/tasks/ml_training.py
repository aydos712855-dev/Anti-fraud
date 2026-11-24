"""Celery tasks: ML model retraining."""
import asyncio
import logging

logger = logging.getLogger(__name__)


def retrain_all_models():
    """Retrain bot detector and other ML models on latest labeled data."""
    async def _run():
        from app.db import AsyncSessionLocal
        from app.models.database import Trade, Alert, AlertStatus, ViolationType
        from app.modules.ml.bot_detector import BotDetector
        from sqlalchemy import select
        import pandas as pd
        from datetime import datetime, timedelta, timezone

        async with AsyncSessionLocal() as db:
            since = datetime.now(timezone.utc) - timedelta(days=90)
            result = await db.execute(
                select(Trade).where(Trade.open_time >= since)
            )
            trades = result.scalars().all()

            # Group by account
            trades_by_account: dict = {}
            for t in trades:
                trades_by_account.setdefault(t.account_id, []).append({
                    "open_time": t.open_time,
                    "volume": float(t.volume),
                    "direction": t.direction,
                    "profit": float(t.profit) if t.profit else None,
                    "duration_ms": t.duration_ms,
                })
            trades_df_map = {
                k: pd.DataFrame(v) for k, v in trades_by_account.items()
            }

            # Labels from confirmed alerts
            result2 = await db.execute(
                select(Alert).where(
                    Alert.status == AlertStatus.confirmed,
                    Alert.violation_type == ViolationType.trading_robot,
                )
            )
            robot_alerts = result2.scalars().all()
            labels = {a.account_id: 1 for a in robot_alerts}

            detector = BotDetector()
            detector.train(trades_df_map, labels)
            logger.info("ML retraining complete: %d accounts", len(trades_df_map))

    asyncio.run(_run())
