from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    app_env: str = "development"
    secret_key: str = "dev_secret_key_change_in_prod"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://antifrod:antifrod_secret@localhost:5432/antifrod"
    redis_url: str = "redis://localhost:6379/0"

    match_trader_api_url: str = ""
    match_trader_api_key: str = ""

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""

    economic_calendar_api_key: str = ""
    reference_feed_url: str = ""

    ml_retrain_cron: str = "0 3 * * *"
    models_storage_path: str = "models_storage"

    # Rule defaults (editable via UI)
    news_window_minutes: int = 3
    margin_threshold_pct: float = 70.0
    hedge_time_window_seconds: int = 60
    hedge_volume_tolerance_pct: float = 10.0
    bot_min_interval_ms: int = 100
    arbitrage_max_duration_seconds: int = 5

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()
