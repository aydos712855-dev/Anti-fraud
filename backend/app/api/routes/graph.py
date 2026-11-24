"""Account connection graph API."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db import get_db
from app.models.database import Account, AccountLink, User, UserRole
from app.modules.graph.builder import GraphBuilder
from app.api.auth import get_current_user, require_role

router = APIRouter()


@router.get("/{account_id}")
async def get_account_graph(
    account_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return graph visualization data for an account's cluster."""
    graph = GraphBuilder(db)
    return await graph.get_cluster_graph(account_id)


@router.post("/{account_id}/rebuild")
async def rebuild_account_links(
    account_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.admin, UserRole.risk_manager])),
):
    """Rebuild entity links for an account."""
    graph = GraphBuilder(db)
    links = await graph.rebuild_links_for_account(account_id)
    return {"links_created_or_updated": len(links)}


@router.post("/links/{link_id}/confirm")
async def confirm_link(
    link_id: int,
    is_related: bool,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.admin, UserRole.risk_manager])),
):
    """Manually confirm or dismiss a link (e.g. family sharing same IP)."""
    from datetime import datetime, timezone
    result = await db.execute(select(AccountLink).where(AccountLink.id == link_id))
    link = result.scalar_one_or_none()
    if not link:
        raise HTTPException(404, "Link not found")

    link.is_confirmed_related = is_related
    link.confirmed_by = current_user.id
    link.confirmed_at = datetime.now(timezone.utc)
    await db.commit()
    return {"link_id": link_id, "confirmed_related": is_related}
