"""Payout check API."""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db import get_db
from app.models.database import PayoutCheck, PayoutDecision, User, UserRole, AuditLog
from app.modules.payout.checker import PayoutChecker
from app.api.auth import get_current_user, require_role

router = APIRouter()


class PayoutCheckResponse(BaseModel):
    id: int
    payment_id: int
    account_id: int
    risk_score: float
    risk_level: str
    recommendation: str
    violations_found: list
    related_accounts: list
    report: dict
    final_decision: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class PayoutDecisionRequest(BaseModel):
    decision: PayoutDecision
    comment: Optional[str] = None


@router.post("/check/{payment_id}", response_model=PayoutCheckResponse)
async def run_payout_check(
    payment_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role([
        UserRole.admin, UserRole.risk_manager, UserRole.finance
    ])),
):
    """Trigger pre-payout risk check for a payment request."""
    checker = PayoutChecker(db)
    try:
        check = await checker.check(payment_id)
        await db.commit()
        return check
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/{check_id}", response_model=PayoutCheckResponse)
async def get_payout_check(
    check_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(PayoutCheck).where(PayoutCheck.id == check_id))
    check = result.scalar_one_or_none()
    if not check:
        raise HTTPException(404, "Payout check not found")
    return check


@router.post("/{check_id}/decide")
async def record_payout_decision(
    check_id: int,
    body: PayoutDecisionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role([
        UserRole.admin, UserRole.risk_manager, UserRole.finance
    ])),
):
    """Record a human decision on the payout check."""
    result = await db.execute(select(PayoutCheck).where(PayoutCheck.id == check_id))
    check = result.scalar_one_or_none()
    if not check:
        raise HTTPException(404, "Payout check not found")

    check.final_decision = body.decision
    check.decided_by = current_user.id
    check.decision_comment = body.comment
    check.decided_at = datetime.now(timezone.utc)

    db.add(AuditLog(
        user_id=current_user.id,
        action="payout_decision",
        entity_type="payout_check",
        entity_id=str(check_id),
        new_value={"decision": body.decision.value, "comment": body.comment},
        created_at=datetime.now(timezone.utc),
    ))

    await db.commit()
    return {"id": check_id, "decision": body.decision}
