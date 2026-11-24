"""Seed script: fills DB with realistic test data for anti-fraud demo."""
import asyncio
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.db import AsyncSessionLocal
from app.models.database import (
    Client, Account, AccountStatus, Trade, Payment,
    Alert, AlertStatus, ViolationType, ViolationCategory,
    RiskLevel, RiskScore, AccountLink, Rule,
)

NOW = datetime.now(timezone.utc)

def dt(days_ago=0, hours_ago=0):
    return NOW - timedelta(days=days_ago, hours=hours_ago)


async def seed():
    async with AsyncSessionLocal() as db:

        # ── Clients ──────────────────────────────────────────────────────
        clients = []
        for i, (name, email, country) in enumerate([
            ("Алибек Жаксыбеков", "alibek@gmail.com",    "KZ"),
            ("Дмитрий Петров",    "dpetrov@mail.ru",     "RU"),
            ("Anar Seitkali",     "anar.s@example.com",  "KZ"),
            ("Nurzhan Bekova",    "nbekova@kz.com",       "KZ"),
            ("Test Bot User",     "bot123@tempmail.com",  "KZ"),
            ("Signal Copier",     "signal_x@proxy.net",   "RU"),
        ], start=1):
            c = Client(
                external_id=f"CLT{i:04d}",
                name=name,
                email=email,
                country=country,
                kyc_status="verified",
                partner_id="PARTNER01",
                promo_code="PROMO2024" if i % 2 == 0 else None,
                created_at=dt(days_ago=90 - i*5),
                updated_at=dt(days_ago=1),
            )
            db.add(c)
            clients.append(c)

        await db.flush()

        # ── Accounts ─────────────────────────────────────────────────────
        accounts = []
        account_data = [
            (clients[0], "ACC001", "challenge", 1,    AccountStatus.active,     10000),
            (clients[0], "ACC002", "funded",    None, AccountStatus.active,     50000),
            (clients[1], "ACC003", "challenge", 1,    AccountStatus.active,     10000),
            (clients[2], "ACC004", "challenge", 2,    AccountStatus.active,     25000),
            (clients[3], "ACC005", "funded",    None, AccountStatus.active,    100000),
            (clients[4], "ACC006", "challenge", 1,    AccountStatus.blocked,    10000),  # bot
            (clients[5], "ACC007", "funded",    None, AccountStatus.active,     50000),  # signal
            (clients[1], "ACC008", "funded",    None, AccountStatus.active,     50000),  # multiaccounting
        ]
        for cli, login, atype, step, status, bal in account_data:
            a = Account(
                login=login,
                client_id=cli.id,
                account_type=atype,
                challenge_step=step,
                status=status,
                balance=Decimal(str(bal)),
                initial_balance=Decimal(str(bal)),
                daily_drawdown_pct=5.0,
                max_drawdown_pct=10.0,
                created_at=dt(days_ago=60),
                updated_at=dt(days_ago=1),
            )
            db.add(a)
            accounts.append(a)

        await db.flush()

        # ── Trades ───────────────────────────────────────────────────────
        symbols = ["EURUSD", "GBPUSD", "XAUUSD", "USDJPY", "BTCUSD"]
        trade_id = 1

        def make_trade(account, days_ago, hours_ago, symbol, direction,
                       volume, profit, duration_ms=60000):
            nonlocal trade_id
            open_t = dt(days_ago=days_ago, hours_ago=hours_ago)
            t = Trade(
                external_id=f"TRD{trade_id:06d}",
                account_id=account.id,
                symbol=symbol,
                direction=direction,
                volume=Decimal(str(volume)),
                open_price=Decimal("1.08500"),
                close_price=Decimal("1.08600"),
                profit=Decimal(str(round(profit, 2))),
                open_time=open_t,
                close_time=open_t + timedelta(milliseconds=duration_ms),
                duration_ms=duration_ms,
            )
            trade_id += 1
            db.add(t)
            return t

        # Normal trading – ACC001
        for i in range(20):
            make_trade(accounts[0], days_ago=random.randint(1, 25),
                       hours_ago=random.randint(0, 8),
                       symbol=random.choice(symbols),
                       direction=random.choice(["buy", "sell"]),
                       volume=0.1, profit=random.uniform(-50, 100),
                       duration_ms=random.randint(300000, 3600000))

        # Bot trading – ACC006: ultra-fast trades, regular intervals, high win rate
        for i in range(60):
            make_trade(accounts[5], days_ago=random.randint(1, 10),
                       hours_ago=random.randint(0, 23),
                       symbol="EURUSD", direction="buy",
                       volume=0.1, profit=abs(random.uniform(5, 25)),
                       duration_ms=random.randint(2000, 4500))

        # Signal copying – ACC007 mirrors ACC004 on same symbol+direction+time
        for i in range(15):
            d = random.randint(1, 15)
            h = random.randint(0, 20)
            sym = random.choice(["EURUSD", "GBPUSD"])
            make_trade(accounts[6], days_ago=d, hours_ago=h,
                       symbol=sym, direction="buy",
                       volume=0.5, profit=random.uniform(50, 200),
                       duration_ms=180000)
            make_trade(accounts[3], days_ago=d, hours_ago=h,
                       symbol=sym, direction="buy",
                       volume=0.5, profit=random.uniform(50, 200),
                       duration_ms=180000)

        # Multiaccounting: ACC003 and ACC008 (same client)
        for i in range(10):
            make_trade(accounts[2], days_ago=random.randint(1, 20),
                       hours_ago=random.randint(0, 8),
                       symbol="XAUUSD", direction="buy",
                       volume=1.0, profit=random.uniform(-100, 300),
                       duration_ms=7200000)
            make_trade(accounts[7], days_ago=random.randint(1, 20),
                       hours_ago=random.randint(0, 8),
                       symbol="XAUUSD", direction="sell",
                       volume=1.0, profit=random.uniform(-100, 300),
                       duration_ms=7200000)

        await db.flush()

        # ── Payments ─────────────────────────────────────────────────────
        for i, (cli, atype, amount) in enumerate([
            (clients[0], "payout",   3000),
            (clients[1], "payout",   8000),
            (clients[2], "purchase",  500),
            (clients[3], "payout",  15000),
            (clients[4], "payout",    500),
            (clients[5], "payout",  12000),
        ]):
            p = Payment(
                external_id=f"PAY{i+1:04d}",
                client_id=cli.id,
                account_id=accounts[i].id,
                payment_type=atype,
                amount=Decimal(str(amount)),
                currency="USD",
                card_bin="424242",
                card_country="KZ",
                status="pending",
                promo_code="PROMO2024" if i % 3 == 0 else None,
                created_at=dt(days_ago=random.randint(1, 10)),
            )
            db.add(p)

        await db.flush()

        # ── Account Links ─────────────────────────────────────────────────
        link1 = AccountLink(
            account_a_id=accounts[2].id,  # ACC003
            account_b_id=accounts[7].id,  # ACC008
            weight=0.85,
            link_reasons=["same_kyc_name", "same_ip", "same_card_bin"],
            is_confirmed_related=True,
            confirmed_at=dt(days_ago=2),
            created_at=dt(days_ago=5),
        )
        db.add(link1)

        link2 = AccountLink(
            account_a_id=accounts[6].id,  # ACC007
            account_b_id=accounts[3].id,  # ACC004
            weight=0.65,
            link_reasons=["same_device_fingerprint", "same_ip"],
            is_confirmed_related=None,
            created_at=dt(days_ago=3),
        )
        db.add(link2)

        await db.flush()

        # ── Alerts ───────────────────────────────────────────────────────
        alert_data = [
            (accounts[5], ViolationType.trading_robot,   ViolationCategory.hard,         RiskLevel.critical, AlertStatus.confirmed,       dt(days_ago=5)),
            (accounts[2], ViolationType.multiaccounting, ViolationCategory.hard,         RiskLevel.critical, AlertStatus.confirmed,       dt(days_ago=4)),
            (accounts[7], ViolationType.multiaccounting, ViolationCategory.hard,         RiskLevel.high,     AlertStatus.confirmed,       dt(days_ago=4)),
            (accounts[6], ViolationType.group_trading,   ViolationCategory.hard,         RiskLevel.high,     AlertStatus.in_review,       dt(days_ago=3)),
            (accounts[3], ViolationType.group_trading,   ViolationCategory.hard,         RiskLevel.high,     AlertStatus.in_review,       dt(days_ago=3)),
            (accounts[0], ViolationType.news_trading,    ViolationCategory.soft,         RiskLevel.medium,   AlertStatus.new,             dt(days_ago=2)),
            (accounts[1], ViolationType.margin_overuse,  ViolationCategory.soft,         RiskLevel.low,      AlertStatus.false_positive,  dt(days_ago=7)),
            (accounts[4], ViolationType.lucky_strategy,  ViolationCategory.risk_pattern, RiskLevel.medium,   AlertStatus.new,             dt(days_ago=1)),
        ]
        for acc, vtype, cat, rlevel, status, created in alert_data:
            a = Alert(
                account_id=acc.id,
                violation_type=vtype,
                category=cat,
                risk_level=rlevel,
                status=status,
                details={"auto": True, "description": f"Detected {vtype.value}"},
                evidence_trade_ids=[],
                created_at=created,
                updated_at=created,
            )
            db.add(a)

        await db.flush()

        # ── Risk Scores ───────────────────────────────────────────────────
        score_data = [
            (accounts[5], None, 92.0, RiskLevel.critical, {"rules": 60, "ml": 20, "cluster": 12}),
            (accounts[2], 1,   85.0, RiskLevel.critical, {"rules": 50, "ml": 15, "cluster": 20}),
            (accounts[7], 1,   81.0, RiskLevel.critical, {"rules": 45, "ml": 16, "cluster": 20}),
            (accounts[6], None, 68.0, RiskLevel.high,     {"rules": 40, "ml": 18, "cluster": 10}),
            (accounts[3], None, 65.0, RiskLevel.high,     {"rules": 38, "ml": 17, "cluster": 10}),
            (accounts[4], None, 42.0, RiskLevel.medium,   {"rules": 35, "ml": 7,  "cluster":  0}),
            (accounts[0], None, 18.0, RiskLevel.low,      {"rules": 10, "ml": 5,  "cluster":  3}),
            (accounts[1], None,  5.0, RiskLevel.low,      {"rules":  3, "ml": 2,  "cluster":  0}),
        ]
        for acc, cluster, score, rlevel, breakdown in score_data:
            rs = RiskScore(
                account_id=acc.id,
                client_id=acc.client_id,
                cluster_id=cluster,
                score=score,
                risk_level=rlevel,
                score_breakdown=breakdown,
                computed_at=dt(days_ago=random.randint(0, 2)),
                trigger="trade",
            )
            db.add(rs)

        await db.flush()

        # ── Rules ─────────────────────────────────────────────────────────
        rule_defs = [
            ("Hedge Between Accounts", ViolationType.hedge_between_accounts, ViolationCategory.hard,
             {"max_correlation": 0.9, "min_trades": 5, "window_hours": 24},
             "Accounts trading opposite directions on same symbol simultaneously"),
            ("Bot Detection", ViolationType.trading_robot, ViolationCategory.hard,
             {"min_trades": 20, "max_interval_cv": 0.1, "max_duration_ms": 5000},
             "Ultra-regular trade intervals and fast execution"),
            ("News Trading", ViolationType.news_trading, ViolationCategory.soft,
             {"window_minutes_before": 2, "window_minutes_after": 2, "min_trades": 2},
             "Trading within 2 minutes of high-impact news events"),
            ("Margin Overuse", ViolationType.margin_overuse, ViolationCategory.soft,
             {"max_drawdown_pct": 90.0},
             "Account using over 90% of available margin"),
            ("Promo Abuse", ViolationType.promo_abuse, ViolationCategory.risk_pattern,
             {"max_accounts_per_promo": 3},
             "Same promo code used on multiple related accounts"),
        ]
        for name, vtype, cat, params, desc in rule_defs:
            r = Rule(
                name=name,
                violation_type=vtype,
                category=cat,
                parameters=params,
                description=desc,
                is_active=True,
                version=1,
                created_by=1,
                created_at=dt(days_ago=30),
                updated_at=dt(days_ago=30),
            )
            db.add(r)

        await db.commit()
        print("Seed complete:")
        print(f"  Clients:  {len(clients)}")
        print(f"  Accounts: {len(accounts)}")
        print(f"  Trades:   {trade_id - 1}")
        print(f"  Alerts:   {len(alert_data)}")
        print(f"  Scores:   {len(score_data)}")
        print(f"  Rules:    {len(rule_defs)}")


asyncio.run(seed())
