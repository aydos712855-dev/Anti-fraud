"""Alert management API."""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc

from app.db import get_db
from app.models.database import Alert, AlertStatus, RiskLevel, ViolationType, AuditLog
from app.api.auth import get_current_user, require_role
from app.models.database import User, UserRole

router = APIRouter()


class AlertResponse(BaseModel):
    id: int
    account_id: int
    violation_type: str
    category: str
    risk_level: str
    status: str
    details: dict
    evidence_trade_ids: list
    ml_score: Optional[float]
    created_at: datetime

    class Config:
        from_attributes = True


class AlertUpdateRequest(BaseModel):
    status: AlertStatus
    comment: Optional[str] = None


@router.get("", response_model=list[AlertResponse])
async def list_alerts(
    status: Optional[AlertStatus] = None,
    risk_level: Optional[RiskLevel] = None,
    violation_type: Optional[ViolationType] = None,
    account_id: Optional[int] = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = select(Alert).order_by(desc(Alert.created_at))
    filters = []
    if status:
        filters.append(Alert.status == status)
    if risk_level:
        filters.append(Alert.risk_level == risk_level)
    if violation_type:
        filters.append(Alert.violation_type == violation_type)
    if account_id:
        filters.append(Alert.account_id == account_id)
    if filters:
        query = query.where(and_(*filters))

    result = await db.execute(query.limit(limit).offset(offset))
    return result.scalars().all()


@router.get("/{alert_id}", response_model=AlertResponse)
async def get_alert(
    alert_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Alert).where(Alert.id == alert_id))
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(404, "Alert not found")
    return alert


@router.patch("/{alert_id}")
async def update_alert(
    alert_id: int,
    body: AlertUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.admin, UserRole.risk_manager])),
):
    result = await db.execute(select(Alert).where(Alert.id == alert_id))
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(404, "Alert not found")

    old_status = alert.status
    alert.status = body.status
    alert.updated_at = datetime.now(timezone.utc)
    if body.status in (AlertStatus.confirmed, AlertStatus.false_positive):
        alert.resolved_by = current_user.id
        alert.resolution_comment = body.comment

    # Audit log
    db.add(AuditLog(
        user_id=current_user.id,
        action="update_alert_status",
        entity_type="alert",
        entity_id=str(alert_id),
        old_value={"status": old_status.value},
        new_value={"status": body.status.value, "comment": body.comment},
        created_at=datetime.now(timezone.utc),
    ))

    await db.commit()
    return {"id": alert_id, "status": body.status}


@router.post("/{alert_id}/assign")
async def assign_alert(
    alert_id: int,
    assignee_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.admin, UserRole.risk_manager])),
):
    result = await db.execute(select(Alert).where(Alert.id == alert_id))
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(404, "Alert not found")

    alert.assigned_to = assignee_id
    alert.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"assigned": assignee_id}
