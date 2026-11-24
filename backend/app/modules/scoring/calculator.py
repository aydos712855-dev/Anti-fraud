"""
Risk score calculator.
Computes a 0-100 score for account / client / cluster.
Score = weighted sum of rule violation severity + ML model signals.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc, func

from app.models.database import (
    Account, Alert, RiskScore, Trade,
    ViolationType, ViolationCategory, AlertStatus, RiskLevel
)
from app.modules.ml.bot_detector import BotDetector
from app.modules.ml.anomaly import score_latency_arbitrage
from app.modules.graph.builder import GraphBuilder

logger = logging.getLogger(__name__)

# Violation weights (contribute to score)
VIOLATION_WEIGHTS: dict[str, float] = {
    "hard": 40.0,
    "soft": 15.0,
    "risk_pattern": 7.0,
}

# ML signal weight
ML_WEIGHT = 20.0

# Score thresholds → risk levels
SCORE_THRESHOLDS = {
    RiskLevel.critical: 75.0,
    RiskLevel.high: 50.0,
    RiskLevel.medium: 25.0,
    RiskLevel.low: 0.0,
}

_bot_detector = BotDetector()


def score_to_level(score: float) -> RiskLevel:
    if score >= 75:
        return RiskLevel.critical
    if score >= 50:
        return RiskLevel.high
    if score >= 25:
        return RiskLevel.medium
    return RiskLevel.low


class RiskScoreCalculator:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def compute_account_score(
        self,
        account_id: int,
        trigger: str = "trade",
    ) -> RiskScore:
        """Compute and persist risk score for a single account."""
        breakdown: dict = {}

        # 1. Rule violations in last 90 days
        since = datetime.now(timezone.utc) - timedelta(days=90)
        result = await self.db.execute(
            select(Alert).where(
                and_(
                    Alert.account_id == account_id,
                    Alert.created_at >= since,
                    Alert.status != AlertStatus.false_positive,
                )
            )
        )
        alerts = result.scalars().all()

        rule_score = 0.0
        violation_counts: dict[str, int] = {}
        for alert in alerts:
            w = VIOLATION_WEIGHTS.get(alert.category.value, 5.0)
            # Recency decay: older alerts contribute less
            age_days = (datetime.now(timezone.utc) - alert.created_at).days
            decay = max(1.0 - age_days / 90, 0.1)
            rule_score += w * decay
            vtype = alert.violation_type.value
            violation_counts[vtype] = violation_counts.get(vtype, 0) + 1

        rule_score = min(rule_score, 60.0)
        breakdown["rule_violations"] = {
            "score": round(rule_score, 2),
            "counts": violation_counts,
        }

        # 2. ML signals
        ml_score = await self._compute_ml_score(account_id)
        breakdown["ml_signals"] = {"score": round(ml_score, 2)}

        # 3. Cluster risk transfer: if any linked account is high-risk, add partial score
        cluster_boost = await self._compute_cluster_boost(account_id)
        breakdown["cluster_risk"] = {"score": round(cluster_boost, 2)}

        total = min(rule_score + ml_score + cluster_boost, 100.0)
        level = score_to_level(total)

        result2 = await self.db.execute(
            select(RiskScore).where(
                and_(
                    RiskScore.account_id == account_id,
                    RiskScore.trigger == trigger,
                )
            ).order_by(desc(RiskScore.computed_at)).limit(1)
        )
        # Always create new score record (time-series)
        risk_score = RiskScore(
            account_id=account_id,
            score=round(total, 2),
            risk_level=level,
            score_breakdown=breakdown,
            computed_at=datetime.now(timezone.utc),
            trigger=trigger,
        )
        self.db.add(risk_score)
        await self.db.flush()
        return risk_score

    async def compute_cluster_score(
        self,
        account_id: int,
    ) -> Optional[RiskScore]:
        """Compute aggregate score for the cluster containing account_id."""
        graph = GraphBuilder(self.db)
        cluster_ids = await graph.get_cluster_account_ids(account_id)

        if len(cluster_ids) <= 1:
            return None

        scores = []
        for acc_id in cluster_ids:
            score_obj = await self.compute_account_score(acc_id, trigger="cluster")
            scores.append(score_obj.score)

        # Cluster score = max individual score + bonus for size
        cluster_score = min(max(scores) + len(cluster_ids) * 2.0, 100.0)
        level = score_to_level(cluster_score)

        result = await self.db.execute(
            select(Account.client_id).where(Account.id == account_id)
        )
        client_id = result.scalar_one_or_none()

        risk_score = RiskScore(
            cluster_id=hash(tuple(sorted(cluster_ids))) % (10**9),
            client_id=client_id,
            score=round(cluster_score, 2),
            risk_level=level,
            score_breakdown={
                "cluster_accounts": cluster_ids,
                "individual_scores": scores,
                "max_score": max(scores),
            },
            computed_at=datetime.now(timezone.utc),
            trigger="cluster",
        )
        self.db.add(risk_score)
        await self.db.flush()
        return risk_score

    async def _compute_ml_score(self, account_id: int) -> float:
        """Run ML detectors and return a score contribution 0-20."""
        trades = await self._load_trades_df(account_id)
        if trades.empty:
            return 0.0

        ml_total = 0.0

        # Bot detection
        bot_result = _bot_detector.predict(trades)
        if bot_result["is_bot"]:
            ml_total += ML_WEIGHT * 0.6 * bot_result["score"]

        # Latency arbitrage
        arb_result = score_latency_arbitrage(trades)
        if arb_result["flagged"]:
            ml_total += ML_WEIGHT * 0.4 * arb_result["score"]

        return min(ml_total, ML_WEIGHT)

    async def _compute_cluster_boost(self, account_id: int) -> float:
        """If linked accounts have hard violations, boost this account's score."""
        from app.models.database import AccountLink
        result = await self.db.execute(
            select(AccountLink).where(
                (AccountLink.account_a_id == account_id) |
                (AccountLink.account_b_id == account_id)
            )
        )
        links = result.scalars().all()

        if not links:
            return 0.0

        linked_ids = []
        for link in links:
            other = link.account_b_id if link.account_a_id == account_id else link.account_a_id
            linked_ids.append(other)

        since = datetime.now(timezone.utc) - timedelta(days=30)
        result2 = await self.db.execute(
            select(func.count(Alert.id)).where(
                and_(
                    Alert.account_id.in_(linked_ids),
                    Alert.category == ViolationCategory.hard,
                    Alert.created_at >= since,
                    Alert.status != AlertStatus.false_positive,
                )
            )
        )
        hard_count = result2.scalar() or 0
        return min(hard_count * 5.0, 20.0)

    async def _load_trades_df(self, account_id: int) -> pd.DataFrame:
        since = datetime.now(timezone.utc) - timedelta(days=30)
        result = await self.db.execute(
            select(Trade).where(
                and_(Trade.account_id == account_id, Trade.open_time >= since)
            )
        )
        trades = result.scalars().all()
        if not trades:
            return pd.DataFrame()

        return pd.DataFrame([{
            "open_time": t.open_time,
            "close_time": t.close_time,
            "volume": float(t.volume),
            "direction": t.direction,
            "symbol": t.symbol,
            "profit": float(t.profit) if t.profit else None,
            "duration_ms": t.duration_ms,
            "margin_pct": t.margin_pct,
        } for t in trades])
