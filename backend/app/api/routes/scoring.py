"""Risk scoring API."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.db import get_db
from app.models.database import RiskScore, User
from app.modules.scoring.calculator import RiskScoreCalculator
from app.api.auth import get_current_user

router = APIRouter()


@router.get("/{account_id}/current")
async def get_current_score(
    account_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(RiskScore)
        .where(RiskScore.account_id == account_id)
        .order_by(desc(RiskScore.computed_at))
        .limit(1)
    )
    score = result.scalar_one_or_none()
    if not score:
        return {"account_id": account_id, "score": 0, "risk_level": "low", "computed_at": None}
    return score


@router.post("/{account_id}/compute")
async def compute_score(
    account_id: int,
    trigger: str = "manual",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Force recompute risk score for an account."""
    calc = RiskScoreCalculator(db)
    score = await calc.compute_account_score(account_id, trigger=trigger)
    await db.commit()
    return {
        "account_id": account_id,
        "score": score.score,
        "risk_level": score.risk_level.value,
        "breakdown": score.score_breakdown,
        "computed_at": score.computed_at,
    }


@router.get("/{account_id}/history")
async def get_score_history(
    account_id: int,
    limit: int = 30,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(RiskScore)
        .where(RiskScore.account_id == account_id)
        .order_by(desc(RiskScore.computed_at))
        .limit(limit)
    )
    return result.scalars().all()
