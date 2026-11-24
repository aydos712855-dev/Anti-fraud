"""
Entity resolution and account linkage graph.
Builds a weighted undirected graph of accounts connected by shared attributes.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import networkx as nx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_

from app.models.database import Account, Client, Session, Payment, AccountLink

logger = logging.getLogger(__name__)

# Weight contribution per shared attribute
LINK_WEIGHTS = {
    "same_ip": 0.4,
    "same_subnet": 0.2,
    "same_device_fingerprint": 0.5,
    "same_card_bin": 0.3,
    "same_wallet": 0.5,
    "similar_email": 0.3,
    "same_kyc_name": 0.4,
    "same_promo_code": 0.2,
    "login_time_overlap": 0.15,
}

# Minimum weight to persist a link
MIN_LINK_WEIGHT = 0.3


class GraphBuilder:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def rebuild_links_for_account(self, account_id: int) -> list[AccountLink]:
        """
        Compute links between `account_id` and all other accounts.
        Upserts AccountLink rows and returns the list.
        """
        account = await self._get_account(account_id)
        if not account:
            return []

        client = await self._get_client(account.client_id)
        sessions = await self._get_sessions(account.client_id)
        payments = await self._get_payments(account.client_id)

        all_accounts = await self._get_all_active_accounts(exclude_client=account.client_id)
        new_links = []

        for other in all_accounts:
            reasons, weight = await self._compute_similarity(
                account, client, sessions, payments, other
            )
            if weight < MIN_LINK_WEIGHT:
                continue

            link = await self._upsert_link(account_id, other.id, weight, reasons)
            new_links.append(link)

        await self.db.commit()
        return new_links

    async def get_cluster_graph(self, account_id: int) -> dict:
        """
        Return a serializable graph dict for visualization (nodes + edges).
        Performs BFS from account_id across the link graph.
        """
        result = await self.db.execute(select(AccountLink))
        all_links = result.scalars().all()

        G = nx.Graph()
        for link in all_links:
            G.add_edge(
                link.account_a_id,
                link.account_b_id,
                weight=link.weight,
                reasons=link.link_reasons,
            )

        if account_id not in G:
            return {"nodes": [], "edges": []}

        # BFS to get connected component
        component = nx.node_connected_component(G, account_id)
        subgraph = G.subgraph(component)

        nodes = []
        for node in subgraph.nodes():
            acc = await self._get_account(node)
            nodes.append({
                "id": node,
                "login": acc.login if acc else str(node),
                "status": acc.status if acc else "unknown",
                "is_root": node == account_id,
            })

        edges = []
        for u, v, data in subgraph.edges(data=True):
            edges.append({
                "source": u,
                "target": v,
                "weight": round(data["weight"], 2),
                "reasons": data["reasons"],
                "label": ", ".join(data["reasons"]),
            })

        return {"nodes": nodes, "edges": edges}

    async def get_cluster_account_ids(self, account_id: int) -> list[int]:
        """Return all account IDs in the same cluster."""
        result = await self.db.execute(select(AccountLink))
        all_links = result.scalars().all()

        G = nx.Graph()
        for link in all_links:
            G.add_edge(link.account_a_id, link.account_b_id)

        if account_id not in G:
            return [account_id]

        return list(nx.node_connected_component(G, account_id))

    # ── Similarity computation ────────────────────────────────────────────────

    async def _compute_similarity(
        self,
        account: Account,
        client: Client,
        sessions: list[Session],
        payments: list[Payment],
        other: Account,
    ) -> tuple[list[str], float]:
        reasons = []
        weight = 0.0

        other_client = await self._get_client(other.client_id)
        other_sessions = await self._get_sessions(other.client_id)
        other_payments = await self._get_payments(other.client_id)

        my_ips = {s.ip_address for s in sessions if s.ip_address}
        other_ips = {s.ip_address for s in other_sessions if s.ip_address}
        if my_ips & other_ips:
            reasons.append("same_ip")
            weight += LINK_WEIGHTS["same_ip"]

        my_subnets = {s.ip_subnet for s in sessions if s.ip_subnet}
        other_subnets = {s.ip_subnet for s in other_sessions if s.ip_subnet}
        if my_subnets & other_subnets and "same_ip" not in reasons:
            reasons.append("same_subnet")
            weight += LINK_WEIGHTS["same_subnet"]

        my_fp = {s.device_fingerprint for s in sessions if s.device_fingerprint}
        other_fp = {s.device_fingerprint for s in other_sessions if s.device_fingerprint}
        if my_fp & other_fp:
            reasons.append("same_device_fingerprint")
            weight += LINK_WEIGHTS["same_device_fingerprint"]

        my_bins = {p.card_bin for p in payments if p.card_bin}
        other_bins = {p.card_bin for p in other_payments if p.card_bin}
        if my_bins & other_bins:
            reasons.append("same_card_bin")
            weight += LINK_WEIGHTS["same_card_bin"]

        my_wallets = {p.wallet_id for p in payments if p.wallet_id}
        other_wallets = {p.wallet_id for p in other_payments if p.wallet_id}
        if my_wallets & other_wallets:
            reasons.append("same_wallet")
            weight += LINK_WEIGHTS["same_wallet"]

        if client and other_client:
            if self._email_similar(client.email, other_client.email):
                reasons.append("similar_email")
                weight += LINK_WEIGHTS["similar_email"]

            if (client.name and other_client.name
                    and client.name.lower().strip() == other_client.name.lower().strip()):
                reasons.append("same_kyc_name")
                weight += LINK_WEIGHTS["same_kyc_name"]

        my_promos = {p.promo_code for p in payments if p.promo_code}
        other_promos = {p.promo_code for p in other_payments if p.promo_code}
        if my_promos & other_promos:
            reasons.append("same_promo_code")
            weight += LINK_WEIGHTS["same_promo_code"]

        return reasons, min(weight, 1.0)

    def _email_similar(self, email_a: str, email_b: str) -> bool:
        """Check if emails share the same local part (ignoring +tags and dots)."""
        def normalize(e: str) -> str:
            local = e.split("@")[0].split("+")[0].replace(".", "").lower()
            return local
        return normalize(email_a) == normalize(email_b) and email_a != email_b

    # ── DB helpers ────────────────────────────────────────────────────────────

    async def _get_account(self, account_id: int) -> Optional[Account]:
        result = await self.db.execute(select(Account).where(Account.id == account_id))
        return result.scalar_one_or_none()

    async def _get_client(self, client_id: int) -> Optional[Client]:
        result = await self.db.execute(select(Client).where(Client.id == client_id))
        return result.scalar_one_or_none()

    async def _get_sessions(self, client_id: int) -> list[Session]:
        result = await self.db.execute(
            select(Session).where(Session.client_id == client_id)
        )
        return result.scalars().all()

    async def _get_payments(self, client_id: int) -> list[Payment]:
        result = await self.db.execute(
            select(Payment).where(Payment.client_id == client_id)
        )
        return result.scalars().all()

    async def _get_all_active_accounts(self, exclude_client: int) -> list[Account]:
        result = await self.db.execute(
            select(Account).where(Account.client_id != exclude_client)
        )
        return result.scalars().all()

    async def _upsert_link(
        self,
        account_a_id: int,
        account_b_id: int,
        weight: float,
        reasons: list[str],
    ) -> AccountLink:
        a, b = min(account_a_id, account_b_id), max(account_a_id, account_b_id)
        result = await self.db.execute(
            select(AccountLink).where(
                and_(AccountLink.account_a_id == a, AccountLink.account_b_id == b)
            )
        )
        link = result.scalar_one_or_none()

        if link:
            link.weight = weight
            link.link_reasons = reasons
        else:
            link = AccountLink(
                account_a_id=a,
                account_b_id=b,
                weight=weight,
                link_reasons=reasons,
                created_at=datetime.now(timezone.utc),
            )
            self.db.add(link)

        return link
