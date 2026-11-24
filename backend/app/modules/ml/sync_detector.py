"""
Synchronized trading and signal-copying detector.
Finds groups of accounts that trade the same instruments at nearly the same time
with matching SL/TP and volume.
Uses pairwise similarity scoring across recent trade sequences.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from itertools import combinations

import numpy as np
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.models.database import Trade, Account, Alert, ViolationType, ViolationCategory, AlertStatus, RiskLevel

logger = logging.getLogger(__name__)

# Thresholds
TIME_WINDOW_S = 10       # trades within 10s considered synchronous
SIMILARITY_THRESHOLD = 0.75
MIN_SYNC_PAIRS = 3       # minimum synchronized trade pairs to flag


def _trade_sequence_vector(trades: list[Trade], window_minutes: int = 1440) -> np.ndarray:
    """
    Encode a list of trades as a fixed-length time-series vector
    for similarity comparison.
    """
    if not trades:
        return np.zeros(0)

    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=window_minutes)
    bins = 288  # 5-minute bins over 24h

    vec = np.zeros(bins)
    for trade in trades:
        if trade.open_time < start:
            continue
        offset = (trade.open_time - start).total_seconds() / 60  # minutes
        bin_idx = int(offset / (window_minutes / bins))
        if 0 <= bin_idx < bins:
            vec[bin_idx] += float(trade.volume)

    return vec


def _cosine_similarity(v1: np.ndarray, v2: np.ndarray) -> float:
    if v1.shape != v2.shape or not v1.any() or not v2.any():
        return 0.0
    dot = np.dot(v1, v2)
    norm = np.linalg.norm(v1) * np.linalg.norm(v2)
    return float(dot / (norm + 1e-9))


class SyncDetector:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def find_synchronized_groups(
        self,
        account_ids: list[int],
        hours: int = 24,
    ) -> list[dict]:
        """
        Among `account_ids`, find pairs/groups that trade in sync.
        Returns list of suspected sync groups with evidence.
        """
        since = datetime.now(timezone.utc) - timedelta(hours=hours)

        # Load trades for all accounts
        account_trades: dict[int, list[Trade]] = {}
        for acc_id in account_ids:
            result = await self.db.execute(
                select(Trade).where(
                    and_(Trade.account_id == acc_id, Trade.open_time >= since)
                ).order_by(Trade.open_time)
            )
            account_trades[acc_id] = result.scalars().all()

        # Pairwise similarity
        sync_pairs = []
        for acc_a, acc_b in combinations(account_ids, 2):
            trades_a = account_trades.get(acc_a, [])
            trades_b = account_trades.get(acc_b, [])

            sim_score, evidence = self._compute_pair_similarity(trades_a, trades_b)
            if sim_score >= SIMILARITY_THRESHOLD and len(evidence) >= MIN_SYNC_PAIRS:
                sync_pairs.append({
                    "account_a": acc_a,
                    "account_b": acc_b,
                    "similarity": round(sim_score, 3),
                    "sync_trade_pairs": evidence[:10],
                })

        if not sync_pairs:
            return []

        # Group into clusters
        groups = self._cluster_pairs(sync_pairs, account_ids)
        return groups

    def _compute_pair_similarity(
        self,
        trades_a: list[Trade],
        trades_b: list[Trade],
    ) -> tuple[float, list[dict]]:
        """
        Compute similarity between two trade sequences.
        Returns (score, list_of_matching_pairs).
        """
        if not trades_a or not trades_b:
            return 0.0, []

        # Find temporally close trades on same instrument
        matched = []
        for ta in trades_a:
            for tb in trades_b:
                if ta.symbol != tb.symbol:
                    continue
                dt = abs((ta.open_time - tb.open_time).total_seconds())
                if dt > TIME_WINDOW_S:
                    continue
                vol_diff = abs(float(ta.volume) - float(tb.volume)) / max(float(ta.volume), 1e-9)

                # Same direction and similar volume
                if ta.direction == tb.direction and vol_diff < 0.15:
                    matched.append({
                        "trade_a_id": ta.id,
                        "trade_b_id": tb.id,
                        "symbol": ta.symbol,
                        "time_diff_s": round(dt, 2),
                        "direction": ta.direction,
                        "volume_a": float(ta.volume),
                        "volume_b": float(tb.volume),
                    })

        # Also compute vector cosine similarity for sequence-level matching
        vec_a = _trade_sequence_vector(trades_a)
        vec_b = _trade_sequence_vector(trades_b)
        cosine_sim = _cosine_similarity(vec_a, vec_b)

        pair_score = len(matched) / max(len(trades_a), len(trades_b), 1)
        combined_score = 0.6 * cosine_sim + 0.4 * pair_score

        return combined_score, matched

    def _cluster_pairs(
        self,
        pairs: list[dict],
        all_ids: list[int],
    ) -> list[dict]:
        """Group sync pairs into clusters using union-find."""
        parent = {i: i for i in all_ids}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x, y):
            parent[find(x)] = find(y)

        for p in pairs:
            union(p["account_a"], p["account_b"])

        clusters: dict[int, list[int]] = {}
        for acc_id in all_ids:
            root = find(acc_id)
            clusters.setdefault(root, []).append(acc_id)

        groups = []
        for root, members in clusters.items():
            if len(members) < 2:
                continue
            group_pairs = [
                p for p in pairs
                if find(p["account_a"]) == root and find(p["account_b"]) == root
            ]
            groups.append({
                "accounts": members,
                "avg_similarity": round(
                    sum(p["similarity"] for p in group_pairs) / max(len(group_pairs), 1), 3
                ),
                "pairs": group_pairs,
            })

        return groups

    async def create_alerts_for_groups(
        self,
        groups: list[dict],
        rule_id: int | None = None,
    ) -> list[Alert]:
        """Create Alert rows for detected synchronized groups."""
        from datetime import datetime, timezone
        alerts = []
        for group in groups:
            for acc_id in group["accounts"]:
                alert = Alert(
                    account_id=acc_id,
                    violation_type=ViolationType.group_trading,
                    category=ViolationCategory.hard,
                    risk_level=RiskLevel.high,
                    status=AlertStatus.new,
                    rule_id=rule_id,
                    details={
                        "group_accounts": group["accounts"],
                        "avg_similarity": group["avg_similarity"],
                        "sync_pairs_count": sum(len(p["sync_trade_pairs"]) for p in group["pairs"]),
                    },
                    evidence_trade_ids=[
                        tid
                        for p in group["pairs"]
                        for pair in p["sync_trade_pairs"]
                        for tid in [pair["trade_a_id"], pair["trade_b_id"]]
                    ][:50],
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
                self.db.add(alert)
                alerts.append(alert)

        await self.db.flush()
        return alerts
