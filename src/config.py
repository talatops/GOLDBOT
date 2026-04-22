from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    bot_owner_id: int
    groq_api_key: str
    openrouter_api_key: str
    openrouter_model: str
    default_timezone: str
    global_news_cron: str
    database_url: str
    webhook_url: str | None
    webhook_secret: str | None
    polling_mode: bool

    @property
    def database_path(self) -> Path:
        prefix = "sqlite:///"
        if self.database_url.startswith(prefix):
            return Path(self.database_url[len(prefix) :])
        return Path("data/bot.db")


def load_settings() -> Settings:
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    owner = os.getenv("BOT_OWNER_ID", "").strip()
    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN is required.")
    if not owner:
        raise ValueError("BOT_OWNER_ID is required.")
    return Settings(
        telegram_bot_token=token,
        bot_owner_id=int(owner),
        groq_api_key=groq_key,
        openrouter_api_key=openrouter_key,
        openrouter_model=os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini").strip(),
        default_timezone=os.getenv("DEFAULT_TIMEZONE", "UTC").strip(),
        global_news_cron=os.getenv("GLOBAL_NEWS_CRON", "0 9 * * *").strip(),
        database_url=os.getenv("DATABASE_URL", "sqlite:///data/bot.db").strip(),
        webhook_url=os.getenv("WEBHOOK_URL", "").strip() or None,
        webhook_secret=os.getenv("WEBHOOK_SECRET_TOKEN", "").strip() or None,
        polling_mode=os.getenv("POLLING_MODE", "true").strip().lower() == "true",
    )
