"""SQLAlchemy ORM models."""
from datetime import datetime
from decimal import Decimal
from typing import Optional
from sqlalchemy import (
    BigInteger, Boolean, DateTime, Float, ForeignKey, Integer,
    Numeric, String, Text, JSON, Enum as SAEnum, UniqueConstraint, Index
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
import enum


class Base(DeclarativeBase):
    pass


# ── Enums ────────────────────────────────────────────────────────────────────

class AccountStatus(str, enum.Enum):
    active = "active"
    funded = "funded"
    blocked = "blocked"
    closed = "closed"


class ViolationCategory(str, enum.Enum):
    hard = "hard"
    soft = "soft"
    risk_pattern = "risk_pattern"


class ViolationType(str, enum.Enum):
    hedge_between_accounts = "hedge_between_accounts"
    multiaccounting = "multiaccounting"
    group_trading = "group_trading"
    trading_robot = "trading_robot"
    latency_arbitrage = "latency_arbitrage"
    news_trading = "news_trading"
    margin_overuse = "margin_overuse"
    promo_abuse = "promo_abuse"
    payment_fraud = "payment_fraud"
    lucky_strategy = "lucky_strategy"


class RiskLevel(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class AlertStatus(str, enum.Enum):
    new = "new"
    in_review = "in_review"
    confirmed = "confirmed"
    false_positive = "false_positive"
    closed = "closed"


class PayoutDecision(str, enum.Enum):
    approve = "approve"
    manual_review = "manual_review"
    reject = "reject"


# ── Core entities ─────────────────────────────────────────────────────────────

class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    external_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(256), index=True)
    name: Mapped[Optional[str]] = mapped_column(String(256))
    country: Mapped[Optional[str]] = mapped_column(String(3))
    kyc_status: Mapped[Optional[str]] = mapped_column(String(32))
    partner_id: Mapped[Optional[str]] = mapped_column(String(64))
    promo_code: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    accounts: Mapped[list["Account"]] = relationship(back_populates="client")
    risk_scores: Mapped[list["RiskScore"]] = relationship(foreign_keys="RiskScore.client_id")


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    login: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    account_type: Mapped[str] = mapped_column(String(32))  # challenge/funded
    challenge_step: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[AccountStatus] = mapped_column(SAEnum(AccountStatus), index=True)
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    initial_balance: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    daily_drawdown_pct: Mapped[float] = mapped_column(Float, default=5.0)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, default=8.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    client: Mapped["Client"] = relationship(back_populates="accounts")
    trades: Mapped[list["Trade"]] = relationship(back_populates="account")
    risk_scores: Mapped[list["RiskScore"]] = relationship(back_populates="account")


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    external_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    direction: Mapped[str] = mapped_column(String(4))  # buy/sell
    volume: Mapped[Decimal] = mapped_column(Numeric(18, 5))
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    close_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    open_price: Mapped[Decimal] = mapped_column(Numeric(18, 5))
    close_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 5))
    sl: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 5))
    tp: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 5))
    profit: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))
    margin_used: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))
    margin_pct: Mapped[Optional[float]] = mapped_column(Float)
    duration_ms: Mapped[Optional[int]] = mapped_column(BigInteger)

    account: Mapped["Account"] = relationship(back_populates="trades")

    __table_args__ = (
        Index("ix_trades_account_time", "account_id", "open_time"),
    )


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45))
    ip_subnet: Mapped[Optional[str]] = mapped_column(String(45))
    user_agent: Mapped[Optional[str]] = mapped_column(Text)
    device_fingerprint: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    geo_country: Mapped[Optional[str]] = mapped_column(String(3))
    login_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    external_id: Mapped[str] = mapped_column(String(64), unique=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    account_id: Mapped[Optional[int]] = mapped_column(ForeignKey("accounts.id"))
    payment_type: Mapped[str] = mapped_column(String(32))  # purchase/payout
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3))
    card_bin: Mapped[Optional[str]] = mapped_column(String(8))
    card_country: Mapped[Optional[str]] = mapped_column(String(3))
    wallet_id: Mapped[Optional[str]] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32))
    is_chargeback: Mapped[bool] = mapped_column(Boolean, default=False)
    promo_code: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


# ── Rules ─────────────────────────────────────────────────────────────────────

class Rule(Base):
    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    violation_type: Mapped[ViolationType] = mapped_column(SAEnum(ViolationType), index=True)
    category: Mapped[ViolationCategory] = mapped_column(SAEnum(ViolationCategory))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    parameters: Mapped[dict] = mapped_column(JSON)
    description: Mapped[Optional[str]] = mapped_column(Text)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# ── Graph ─────────────────────────────────────────────────────────────────────

class AccountLink(Base):
    """Weighted link between two accounts/clients in the entity graph."""
    __tablename__ = "account_links"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_a_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    account_b_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    weight: Mapped[float] = mapped_column(Float)
    link_reasons: Mapped[list] = mapped_column(JSON)  # ["same_ip", "same_device", ...]
    is_confirmed_related: Mapped[Optional[bool]] = mapped_column(Boolean)
    confirmed_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("account_a_id", "account_b_id"),
        Index("ix_account_links_pair", "account_a_id", "account_b_id"),
    )


# ── Risk scoring ──────────────────────────────────────────────────────────────

class RiskScore(Base):
    __tablename__ = "risk_scores"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[Optional[int]] = mapped_column(ForeignKey("accounts.id"), index=True)
    client_id: Mapped[Optional[int]] = mapped_column(ForeignKey("clients.id"), index=True)
    cluster_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    score: Mapped[float] = mapped_column(Float)
    risk_level: Mapped[RiskLevel] = mapped_column(SAEnum(RiskLevel), index=True)
    score_breakdown: Mapped[dict] = mapped_column(JSON)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    trigger: Mapped[str] = mapped_column(String(64))  # trade/payout/challenge_step

    account: Mapped[Optional["Account"]] = relationship(back_populates="risk_scores", foreign_keys=[account_id])


# ── Alerts & Cases ────────────────────────────────────────────────────────────

class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    violation_type: Mapped[ViolationType] = mapped_column(SAEnum(ViolationType), index=True)
    category: Mapped[ViolationCategory] = mapped_column(SAEnum(ViolationCategory))
    risk_level: Mapped[RiskLevel] = mapped_column(SAEnum(RiskLevel))
    status: Mapped[AlertStatus] = mapped_column(SAEnum(AlertStatus), default=AlertStatus.new, index=True)
    rule_id: Mapped[Optional[int]] = mapped_column(ForeignKey("rules.id"))
    rule_version: Mapped[Optional[int]] = mapped_column(Integer)
    details: Mapped[dict] = mapped_column(JSON)
    evidence_trade_ids: Mapped[list] = mapped_column(JSON, default=list)
    ml_score: Mapped[Optional[float]] = mapped_column(Float)
    ml_features: Mapped[Optional[dict]] = mapped_column(JSON)
    assigned_to: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    resolved_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    resolution_comment: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PayoutCheck(Base):
    __tablename__ = "payout_checks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id"), unique=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    risk_score: Mapped[float] = mapped_column(Float)
    risk_level: Mapped[RiskLevel] = mapped_column(SAEnum(RiskLevel))
    recommendation: Mapped[PayoutDecision] = mapped_column(SAEnum(PayoutDecision))
    violations_found: Mapped[list] = mapped_column(JSON, default=list)
    related_accounts: Mapped[list] = mapped_column(JSON, default=list)
    report: Mapped[dict] = mapped_column(JSON)
    final_decision: Mapped[Optional[PayoutDecision]] = mapped_column(SAEnum(PayoutDecision))
    decided_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    decision_comment: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


# ── Users (risk managers) ─────────────────────────────────────────────────────

class UserRole(str, enum.Enum):
    admin = "admin"
    risk_manager = "risk_manager"
    finance = "finance"
    observer = "observer"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(256))
    full_name: Mapped[str] = mapped_column(String(256))
    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    totp_secret: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(128))
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[Optional[str]] = mapped_column(String(64))
    old_value: Mapped[Optional[dict]] = mapped_column(JSON)
    new_value: Mapped[Optional[dict]] = mapped_column(JSON)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
