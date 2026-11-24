"""
Configurable rules engine for violation detection.
Each rule is stored in DB with versioned parameters.
Managers can edit rules via UI without code changes.
"""
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.models.database import (
    Account, Trade, Rule, Alert, Payment, Session,
    ViolationType, ViolationCategory, AlertStatus, RiskLevel
)

logger = logging.getLogger(__name__)


# ── Default rule parameter schemas ───────────────────────────────────────────

DEFAULT_RULES: list[dict] = [
    {
        "name": "hedge_between_accounts",
        "violation_type": "hedge_between_accounts",
        "category": "hard",
        "parameters": {
            "time_window_seconds": 60,
            "volume_tolerance_pct": 10.0,
            "min_profit_pct_losing": -20.0,
        },
        "description": "Противоположные позиции по одному инструменту на разных связанных счетах",
    },
    {
        "name": "news_trading",
        "violation_type": "news_trading",
        "category": "soft",
        "parameters": {
            "window_minutes": 3,
            "min_impact": "high",
            "affected_currencies": [],
        },
        "description": "Открытие/закрытие позиции в ±3 мин вокруг важных новостей",
    },
    {
        "name": "margin_overuse",
        "violation_type": "margin_overuse",
        "category": "soft",
        "parameters": {
            "threshold_pct": 70.0,
        },
        "description": "Загрузка маржи выше 70% в любой момент торговли",
    },
    {
        "name": "trading_robot",
        "violation_type": "trading_robot",
        "category": "hard",
        "parameters": {
            "min_equal_interval_count": 5,
            "interval_tolerance_ms": 50,
            "min_reaction_speed_ms": 100,
            "round_clock_hours_threshold": 20,
        },
        "description": "Признаки автоматической торговли (советники, боты)",
    },
    {
        "name": "lucky_strategy",
        "violation_type": "lucky_strategy",
        "category": "risk_pattern",
        "parameters": {
            "max_trades_to_pass": 2,
            "min_volume_ratio": 3.0,
        },
        "description": "Прохождение этапа 1-2 сделками максимальным объёмом",
    },
    {
        "name": "promo_abuse",
        "violation_type": "promo_abuse",
        "category": "risk_pattern",
        "parameters": {
            "max_same_promo_uses": 3,
        },
        "description": "Злоупотребление промокодами через связанные аккаунты",
    },
    {
        "name": "payment_fraud",
        "violation_type": "payment_fraud",
        "category": "risk_pattern",
        "parameters": {
            "max_cards_per_client": 3,
            "flag_country_mismatch": True,
        },
        "description": "Оплата чужой картой, несовпадение страны карты/IP/KYC",
    },
]


class RulesEngine:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def run_all_rules(self, account_id: int) -> list[Alert]:
        """Run all active rules for the given account. Returns new alerts."""
        result = await self.db.execute(
            select(Rule).where(Rule.is_active == True)
        )
        rules = result.scalars().all()

        alerts = []
        for rule in rules:
            handler = self._get_handler(rule.violation_type)
            if handler:
                try:
                    new_alerts = await handler(account_id, rule)
                    alerts.extend(new_alerts)
                except Exception:
                    logger.exception("Rule %s failed for account %s", rule.name, account_id)
        return alerts

    def _get_handler(self, vtype: str):
        return {
            "news_trading": self._check_news_trading,
            "margin_overuse": self._check_margin_overuse,
            "hedge_between_accounts": self._check_hedge,
            "trading_robot": self._check_robot_heuristic,
            "lucky_strategy": self._check_lucky_strategy,
            "promo_abuse": self._check_promo_abuse,
            "payment_fraud": self._check_payment_fraud,
        }.get(vtype)

    # ── Rule handlers ─────────────────────────────────────────────────────────

    async def _check_news_trading(self, account_id: int, rule: Rule) -> list[Alert]:
        """Flag trades opened/closed within ±N minutes of a high-impact news event."""
        params = rule.parameters
        window = timedelta(minutes=params.get("window_minutes", 3))

        news_events = await self._get_news_events()
        trades = await self._get_recent_trades(account_id, hours=24)

        flagged_trades = []
        for trade in trades:
            for event in news_events:
                event_time = event["time"]
                if abs((trade.open_time - event_time).total_seconds()) <= window.total_seconds():
                    if self._instrument_related_to_currency(trade.symbol, event["currency"]):
                        flagged_trades.append({
                            "trade_id": trade.id,
                            "event": event["name"],
                            "event_time": event_time.isoformat(),
                            "trade_time": trade.open_time.isoformat(),
                            "symbol": trade.symbol,
                        })

        if not flagged_trades:
            return []

        return [await self._create_alert(
            account_id=account_id,
            violation_type=ViolationType.news_trading,
            rule=rule,
            risk_level=RiskLevel.medium,
            details={"flagged_trades": flagged_trades},
            evidence_trade_ids=[t["trade_id"] for t in flagged_trades],
        )]

    async def _check_margin_overuse(self, account_id: int, rule: Rule) -> list[Alert]:
        """Flag if margin_pct exceeded threshold in any trade."""
        threshold = rule.parameters.get("threshold_pct", 70.0)
        trades = await self._get_recent_trades(account_id, hours=24)

        violations = [
            {"trade_id": t.id, "margin_pct": t.margin_pct, "time": t.open_time.isoformat()}
            for t in trades
            if t.margin_pct is not None and t.margin_pct > threshold
        ]

        if not violations:
            return []

        return [await self._create_alert(
            account_id=account_id,
            violation_type=ViolationType.margin_overuse,
            rule=rule,
            risk_level=RiskLevel.medium,
            details={"violations": violations, "threshold_pct": threshold},
            evidence_trade_ids=[v["trade_id"] for v in violations],
        )]

    async def _check_hedge(self, account_id: int, rule: Rule) -> list[Alert]:
        """
        Find opposite positions on the same symbol opened within time_window
        on accounts linked to this one.
        """
        params = rule.parameters
        window_s = params.get("time_window_seconds", 60)
        vol_tol = params.get("volume_tolerance_pct", 10.0)

        linked_ids = await self._get_linked_account_ids(account_id)
        if not linked_ids:
            return []

        trades_a = await self._get_recent_trades(account_id, hours=48)
        evidence = []

        for other_id in linked_ids:
            trades_b = await self._get_recent_trades(other_id, hours=48)
            for ta in trades_a:
                for tb in trades_b:
                    if ta.symbol != tb.symbol:
                        continue
                    if ta.direction == tb.direction:
                        continue
                    dt = abs((ta.open_time - tb.open_time).total_seconds())
                    if dt > window_s:
                        continue
                    vol_diff = abs(float(ta.volume) - float(tb.volume)) / max(float(ta.volume), 1e-9)
                    if vol_diff * 100 > vol_tol:
                        continue
                    evidence.append({
                        "account_a": account_id,
                        "trade_a": ta.id,
                        "account_b": other_id,
                        "trade_b": tb.id,
                        "symbol": ta.symbol,
                        "time_diff_s": dt,
                    })

        if not evidence:
            return []

        return [await self._create_alert(
            account_id=account_id,
            violation_type=ViolationType.hedge_between_accounts,
            rule=rule,
            risk_level=RiskLevel.critical,
            details={"hedge_pairs": evidence},
            evidence_trade_ids=list({e["trade_a"] for e in evidence}),
        )]

    async def _check_robot_heuristic(self, account_id: int, rule: Rule) -> list[Alert]:
        """Detect equal inter-order intervals (bot signature)."""
        params = rule.parameters
        min_count = params.get("min_equal_interval_count", 5)
        tolerance_ms = params.get("interval_tolerance_ms", 50)

        trades = await self._get_recent_trades(account_id, hours=72)
        if len(trades) < min_count + 1:
            return []

        trades_sorted = sorted(trades, key=lambda t: t.open_time)
        intervals = []
        for i in range(1, len(trades_sorted)):
            dt = (trades_sorted[i].open_time - trades_sorted[i-1].open_time).total_seconds() * 1000
            intervals.append(dt)

        buckets: dict[int, list[float]] = {}
        for iv in intervals:
            bucket = round(iv / tolerance_ms)
            buckets.setdefault(bucket, []).append(iv)

        suspicious = {k: v for k, v in buckets.items() if len(v) >= min_count}
        if not suspicious:
            return []

        return [await self._create_alert(
            account_id=account_id,
            violation_type=ViolationType.trading_robot,
            rule=rule,
            risk_level=RiskLevel.high,
            details={
                "repeated_intervals_ms": {str(k * tolerance_ms): len(v) for k, v in suspicious.items()}
            },
            evidence_trade_ids=[t.id for t in trades_sorted],
        )]

    async def _check_lucky_strategy(self, account_id: int, rule: Rule) -> list[Alert]:
        """Flag account that passed a challenge with very few large-volume trades."""
        params = rule.parameters
        max_trades = params.get("max_trades_to_pass", 2)
        min_vol_ratio = params.get("min_volume_ratio", 3.0)

        result = await self.db.execute(
            select(Account).where(Account.id == account_id)
        )
        account = result.scalar_one_or_none()
        if not account or account.account_type != "challenge":
            return []

        trades = await self._get_recent_trades(account_id, hours=720)
        if not trades:
            return []

        avg_vol = sum(float(t.volume) for t in trades) / len(trades)
        profitable = [t for t in trades if t.profit and t.profit > 0]
        big_wins = [t for t in profitable if float(t.volume) >= avg_vol * min_vol_ratio]

        if len(big_wins) <= max_trades and len(trades) <= max_trades * 2:
            return [await self._create_alert(
                account_id=account_id,
                violation_type=ViolationType.lucky_strategy,
                rule=rule,
                risk_level=RiskLevel.medium,
                details={
                    "total_trades": len(trades),
                    "big_win_trades": len(big_wins),
                    "avg_volume": avg_vol,
                },
                evidence_trade_ids=[t.id for t in big_wins],
            )]
        return []

    async def _check_promo_abuse(self, account_id: int, rule: Rule) -> list[Alert]:
        params = rule.parameters
        max_uses = params.get("max_same_promo_uses", 3)

        result = await self.db.execute(
            select(Account).where(Account.id == account_id)
        )
        account = result.scalar_one_or_none()
        if not account:
            return []

        result2 = await self.db.execute(
            select(Payment).where(
                and_(
                    Payment.client_id == account.client_id,
                    Payment.promo_code != None,
                )
            )
        )
        payments = result2.scalars().all()

        promo_counts: dict[str, int] = {}
        for p in payments:
            promo_counts[p.promo_code] = promo_counts.get(p.promo_code, 0) + 1

        abused = {k: v for k, v in promo_counts.items() if v > max_uses}
        if not abused:
            return []

        return [await self._create_alert(
            account_id=account_id,
            violation_type=ViolationType.promo_abuse,
            rule=rule,
            risk_level=RiskLevel.medium,
            details={"promo_abuse": abused},
            evidence_trade_ids=[],
        )]

    async def _check_payment_fraud(self, account_id: int, rule: Rule) -> list[Alert]:
        params = rule.parameters
        max_cards = params.get("max_cards_per_client", 3)
        flag_mismatch = params.get("flag_country_mismatch", True)

        result = await self.db.execute(
            select(Account).where(Account.id == account_id)
        )
        account = result.scalar_one_or_none()
        if not account:
            return []

        result2 = await self.db.execute(
            select(Payment).where(Payment.client_id == account.client_id)
        )
        payments = result2.scalars().all()

        issues = []
        cards = {p.card_bin for p in payments if p.card_bin}
        if len(cards) > max_cards:
            issues.append({"type": "too_many_cards", "count": len(cards)})

        if flag_mismatch:
            result3 = await self.db.execute(
                select(Account.id).join(Account.client).where(Account.id == account_id)
            )
            # Compare card_country with client KYC country
            for p in payments:
                if p.card_country:
                    client_result = await self.db.execute(
                        select(Account).where(Account.id == account_id)
                    )
                    acc = client_result.scalar_one_or_none()
                    # Simplified: flag if more than one distinct card country
            card_countries = {p.card_country for p in payments if p.card_country}
            if len(card_countries) > 2:
                issues.append({"type": "multiple_card_countries", "countries": list(card_countries)})

        if not issues:
            return []

        return [await self._create_alert(
            account_id=account_id,
            violation_type=ViolationType.payment_fraud,
            rule=rule,
            risk_level=RiskLevel.high,
            details={"issues": issues},
            evidence_trade_ids=[],
        )]

    # ── Helpers ────────────────────────────────────────────────────────────────

    async def _get_recent_trades(self, account_id: int, hours: int = 24) -> list[Trade]:
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        result = await self.db.execute(
            select(Trade).where(
                and_(Trade.account_id == account_id, Trade.open_time >= since)
            ).order_by(Trade.open_time)
        )
        return result.scalars().all()

    async def _get_linked_account_ids(self, account_id: int) -> list[int]:
        from app.models.database import AccountLink
        result = await self.db.execute(
            select(AccountLink).where(
                (AccountLink.account_a_id == account_id) |
                (AccountLink.account_b_id == account_id)
            )
        )
        links = result.scalars().all()
        ids = set()
        for link in links:
            if link.account_a_id == account_id:
                ids.add(link.account_b_id)
            else:
                ids.add(link.account_a_id)
        return list(ids)

    async def _get_news_events(self) -> list[dict]:
        """Return today's high-impact news events. In production, fetches from calendar API."""
        # Stub — real implementation calls economic calendar API
        return []

    def _instrument_related_to_currency(self, symbol: str, currency: str) -> bool:
        return currency.upper() in symbol.upper()

    async def _create_alert(
        self,
        account_id: int,
        violation_type: ViolationType,
        rule: Rule,
        risk_level: RiskLevel,
        details: dict,
        evidence_trade_ids: list[int],
    ) -> Alert:
        from datetime import datetime, timezone
        alert = Alert(
            account_id=account_id,
            violation_type=violation_type,
            category=rule.category,
            risk_level=risk_level,
            status=AlertStatus.new,
            rule_id=rule.id,
            rule_version=rule.version,
            details=details,
            evidence_trade_ids=evidence_trade_ids,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self.db.add(alert)
        await self.db.flush()
        return alert
