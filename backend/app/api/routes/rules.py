"""Rules management API — CRUD + backtest."""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db import get_db
from app.models.database import Rule, ViolationType, ViolationCategory, User, UserRole, AuditLog
from app.api.auth import get_current_user, require_role

router = APIRouter()


class RuleCreate(BaseModel):
    name: str
    violation_type: ViolationType
    category: ViolationCategory
    parameters: dict
    description: Optional[str] = None
    is_active: bool = True


class RuleUpdate(BaseModel):
    parameters: Optional[dict] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class RuleResponse(BaseModel):
    id: int
    name: str
    violation_type: str
    category: str
    is_active: bool
    version: int
    parameters: dict
    description: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


@router.get("", response_model=list[RuleResponse])
async def list_rules(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Rule).order_by(Rule.id))
    return result.scalars().all()


@router.post("", response_model=RuleResponse, status_code=201)
async def create_rule(
    body: RuleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.admin])),
):
    rule = Rule(
        **body.model_dump(),
        version=1,
        created_by=current_user.id,
        updated_by=current_user.id,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.patch("/{rule_id}", response_model=RuleResponse)
async def update_rule(
    rule_id: int,
    body: RuleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.admin, UserRole.risk_manager])),
):
    result = await db.execute(select(Rule).where(Rule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(404, "Rule not found")

    old_params = dict(rule.parameters)
    if body.parameters is not None:
        rule.parameters = body.parameters
        rule.version += 1
    if body.description is not None:
        rule.description = body.description
    if body.is_active is not None:
        rule.is_active = body.is_active

    rule.updated_by = current_user.id
    rule.updated_at = datetime.now(timezone.utc)

    db.add(AuditLog(
        user_id=current_user.id,
        action="update_rule",
        entity_type="rule",
        entity_id=str(rule_id),
        old_value=old_params,
        new_value=body.parameters or {},
        created_at=datetime.now(timezone.utc),
    ))

    await db.commit()
    await db.refresh(rule)
    return rule


@router.post("/{rule_id}/backtest")
async def backtest_rule(
    rule_id: int,
    days: int = 30,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.admin, UserRole.risk_manager])),
):
    """
    Run a rule against historical data and return how many accounts
    would have been flagged and which ones.
    This is a simplified backtest — full implementation in Celery task.
    """
    result = await db.execute(select(Rule).where(Rule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(404, "Rule not found")

    # Kick off async backtest task
    from app.celery_app import run_rule_backtest
    task = run_rule_backtest.delay(rule_id, days)

    return {
        "task_id": task.id,
        "status": "queued",
        "message": f"Backtest for rule '{rule.name}' started (last {days} days)",
    }
