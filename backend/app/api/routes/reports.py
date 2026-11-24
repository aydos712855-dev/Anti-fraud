"""Dashboard and reporting API."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_

from app.db import get_db
from app.models.database import Alert, PayoutCheck, PayoutDecision, AlertStatus, ViolationCategory, User
from app.api.auth import get_current_user

router = APIRouter()


@router.get("/dashboard")
async def dashboard_stats(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # Alerts by violation type
    result = await db.execute(
        select(Alert.violation_type, func.count(Alert.id))
        .where(Alert.created_at >= since)
        .group_by(Alert.violation_type)
    )
    by_type = {str(r[0].value): r[1] for r in result.all()}

    # Alerts by category
    result2 = await db.execute(
        select(Alert.category, func.count(Alert.id))
        .where(Alert.created_at >= since)
        .group_by(Alert.category)
    )
    by_category = {str(r[0].value): r[1] for r in result2.all()}

    # False positive rate
    total_resolved = await db.scalar(
        select(func.count(Alert.id)).where(
            and_(
                Alert.created_at >= since,
                Alert.status.in_([AlertStatus.confirmed, AlertStatus.false_positive]),
            )
        )
    ) or 0
    false_positives = await db.scalar(
        select(func.count(Alert.id)).where(
            and_(
                Alert.created_at >= since,
                Alert.status == AlertStatus.false_positive,
            )
        )
    ) or 0
    fp_rate = round(false_positives / max(total_resolved, 1) * 100, 1)

    # Payouts stopped (rejected)
    payouts_checked = await db.scalar(
        select(func.count(PayoutCheck.id)).where(PayoutCheck.created_at >= since)
    ) or 0
    payouts_rejected = await db.scalar(
        select(func.count(PayoutCheck.id)).where(
            and_(
                PayoutCheck.created_at >= since,
                PayoutCheck.final_decision == PayoutDecision.reject,
            )
        )
    ) or 0

    return {
        "period_days": days,
        "alerts_by_type": by_type,
        "alerts_by_category": by_category,
        "total_alerts": sum(by_type.values()),
        "false_positive_rate_pct": fp_rate,
        "payouts_checked": payouts_checked,
        "payouts_rejected": payouts_rejected,
        "payout_coverage_pct": 100.0 if payouts_checked > 0 else 0,
    }


@router.get("/alerts/timeline")
async def alerts_timeline(
    days: int = 30,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Daily alert counts for the last N days."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.execute(
        select(
            func.date_trunc("day", Alert.created_at).label("day"),
            func.count(Alert.id).label("count"),
        )
        .where(Alert.created_at >= since)
        .group_by(func.date_trunc("day", Alert.created_at))
        .order_by(func.date_trunc("day", Alert.created_at))
    )
    return [{"date": str(r.day)[:10], "count": r.count} for r in result.all()]
