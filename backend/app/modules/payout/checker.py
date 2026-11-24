"""
Pre-payout check module.
Every payout request must pass through here before human review.
Assembles a risk report: violations, cluster, score, recommendation.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.models.database import (
    Account, Alert, Payment, PayoutCheck, RiskScore,
    AlertStatus, PayoutDecision, RiskLevel, ViolationCategory
)
from app.modules.scoring.calculator import RiskScoreCalculator
from app.modules.graph.builder import GraphBuilder

logger = logging.getLogger(__name__)

# Thresholds that drive the recommendation
AUTO_REJECT_SCORE = 75.0
AUTO_MANUAL_SCORE = 40.0
HARD_VIOLATION_BLOCKS = True


class PayoutChecker:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def check(self, payment_id: int) -> PayoutCheck:
        """
        Run full pre-payout check for a payment request.
        Returns a persisted PayoutCheck with recommendation.
        """
        payment = await self._get_payment(payment_id)
        if not payment:
            raise ValueError(f"Payment {payment_id} not found")

        account_id = payment.account_id
        if not account_id:
            raise ValueError(f"Payment {payment_id} has no account")

        # Recompute fresh score
        scorer = RiskScoreCalculator(self.db)
        risk_score = await scorer.compute_account_score(account_id, trigger="payout")
        cluster_score = await scorer.compute_cluster_score(account_id)

        # Gather active violations
        since = datetime.now(timezone.utc) - timedelta(days=180)
        result = await self.db.execute(
            select(Alert).where(
                and_(
                    Alert.account_id == account_id,
                    Alert.created_at >= since,
                    Alert.status.notin_([AlertStatus.false_positive, AlertStatus.closed]),
                )
            ).order_by(Alert.created_at.desc())
        )
        violations = result.scalars().all()

        # Related accounts
        graph = GraphBuilder(self.db)
        cluster_ids = await graph.get_cluster_account_ids(account_id)
        related_accounts = [i for i in cluster_ids if i != account_id]

        # Determine recommendation
        recommendation = self._recommend(
            score=risk_score.score,
            violations=violations,
            cluster_score=cluster_score,
        )

        # Violation summary for report
        violation_summary = [
            {
                "type": a.violation_type.value,
                "category": a.category.value,
                "risk_level": a.risk_level.value,
                "created_at": a.created_at.isoformat(),
                "details": a.details,
            }
            for a in violations
        ]

        report = {
            "account_id": account_id,
            "risk_score": risk_score.score,
            "risk_level": risk_score.risk_level.value,
            "score_breakdown": risk_score.score_breakdown,
            "cluster_score": cluster_score.score if cluster_score else None,
            "cluster_accounts": related_accounts,
            "violations": violation_summary,
            "recommendation": recommendation.value,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        check = PayoutCheck(
            payment_id=payment_id,
            account_id=account_id,
            risk_score=risk_score.score,
            risk_level=risk_score.risk_level,
            recommendation=recommendation,
            violations_found=violation_summary,
            related_accounts=related_accounts,
            report=report,
            created_at=datetime.now(timezone.utc),
        )
        self.db.add(check)
        await self.db.flush()
        return check

    def _recommend(
        self,
        score: float,
        violations: list[Alert],
        cluster_score: RiskScore | None,
    ) -> PayoutDecision:
        # Hard violations → reject immediately
        hard_violations = [v for v in violations if v.category == ViolationCategory.hard]
        if HARD_VIOLATION_BLOCKS and hard_violations:
            return PayoutDecision.reject

        # Cluster has hard violations
        if cluster_score and cluster_score.score >= AUTO_REJECT_SCORE:
            return PayoutDecision.reject

        if score >= AUTO_REJECT_SCORE:
            return PayoutDecision.reject

        if score >= AUTO_MANUAL_SCORE:
            return PayoutDecision.manual_review

        return PayoutDecision.approve

    async def _get_payment(self, payment_id: int) -> Payment | None:
        result = await self.db.execute(select(Payment).where(Payment.id == payment_id))
        return result.scalar_one_or_none()
