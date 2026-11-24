"""Account & trade ingestion API."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db import get_db
from app.models.database import Account, Trade, User
from app.api.auth import get_current_user

router = APIRouter()


class AccountResponse(BaseModel):
    id: int
    login: str
    client_id: int
    account_type: str
    status: str
    balance: float
    created_at: datetime

    class Config:
        from_attributes = True


@router.get("/{login}")
async def get_account_by_login(
    login: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Account).where(Account.login == login))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(404, "Account not found")
    return account


@router.get("/{account_id}/trades")
async def get_account_trades(
    account_id: int,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Trade)
        .where(Trade.account_id == account_id)
        .order_by(Trade.open_time.desc())
        .limit(limit)
    )
    return result.scalars().all()
