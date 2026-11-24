"""
Alert notification sender.
Sends alerts to web UI (via Redis pub/sub), Telegram, and email.
Groups repeated alerts for the same account to avoid channel spam.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import redis.asyncio as aioredis

from app.config import get_settings
from app.models.database import Alert, RiskLevel

logger = logging.getLogger(__name__)
settings = get_settings()

PRIORITY_EMOJI = {
    RiskLevel.critical: "🔴",
    RiskLevel.high: "🟠",
    RiskLevel.medium: "🟡",
    RiskLevel.low: "🟢",
}

DEDUP_WINDOW_MINUTES = 30


class AlertSender:
    def __init__(self, redis_client: aioredis.Redis):
        self.redis = redis_client

    async def send(self, alert: Alert) -> None:
        """Send alert to all channels, respecting dedup window."""
        dedup_key = f"alert_sent:{alert.account_id}:{alert.violation_type.value}"
        already_sent = await self.redis.get(dedup_key)
        if already_sent:
            logger.debug("Alert deduped for account %s type %s", alert.account_id, alert.violation_type)
            return

        # Set dedup flag
        await self.redis.setex(dedup_key, DEDUP_WINDOW_MINUTES * 60, "1")

        message = self._format_message(alert)

        # Publish to WebSocket channel for real-time UI
        await self.redis.publish("alerts", message)

        # Telegram
        if settings.telegram_bot_token and settings.telegram_chat_id:
            await self._send_telegram(message)

        # Email — only for critical/high
        if alert.risk_level in (RiskLevel.critical, RiskLevel.high):
            if settings.smtp_user:
                await self._send_email(alert, message)

    def _format_message(self, alert: Alert) -> str:
        emoji = PRIORITY_EMOJI.get(alert.risk_level, "⚪")
        vtype_names = {
            "hedge_between_accounts": "Хеджирование между счетами",
            "multiaccounting": "Мультиаккаунтинг",
            "group_trading": "Групповая торговля",
            "trading_robot": "Торговый робот",
            "latency_arbitrage": "Арбитраж задержек",
            "news_trading": "Торговля на новостях",
            "margin_overuse": "Превышение маржи 70%",
            "promo_abuse": "Злоупотребление промокодами",
            "payment_fraud": "Платёжный фрод",
            "lucky_strategy": "Стратегия «на удачу»",
        }
        vtype_ru = vtype_names.get(alert.violation_type.value, alert.violation_type.value)
        return (
            f"{emoji} [{alert.risk_level.value.upper()}] {vtype_ru}\n"
            f"Счёт: {alert.account_id} | "
            f"Категория: {alert.category.value}\n"
            f"Время: {alert.created_at.strftime('%Y-%m-%d %H:%M UTC')}"
        )

    async def _send_telegram(self, message: str) -> None:
        try:
            import httpx
            url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
            async with httpx.AsyncClient() as client:
                await client.post(url, json={
                    "chat_id": settings.telegram_chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                })
        except Exception:
            logger.exception("Failed to send Telegram alert")

    async def _send_email(self, alert: Alert, message: str) -> None:
        try:
            import aiosmtplib
            from email.message import EmailMessage
            msg = EmailMessage()
            msg["Subject"] = f"[Антифрод] {alert.risk_level.value.upper()} — счёт {alert.account_id}"
            msg["From"] = settings.smtp_user
            msg["To"] = settings.smtp_user
            msg.set_content(message)
            await aiosmtplib.send(
                msg,
                hostname=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_user,
                password=settings.smtp_password,
                start_tls=True,
            )
        except Exception:
            logger.exception("Failed to send email alert")
