from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    bot_owner_ids: tuple[int, ...]
    groq_api_key: str
    google_api_key: str
    google_model: str
    openrouter_api_key: str
    openrouter_model: str
    default_timezone: str
    global_news_cron: str
    database_url: str
    webhook_url: str | None
    webhook_secret: str | None
    polling_mode: bool

    @property
    def bot_owner_id(self) -> int:
        # Backward-compatible primary owner reference.
        return self.bot_owner_ids[0]

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
    owners_csv = os.getenv("BOT_OWNER_IDS", "").strip()
    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    google_key = os.getenv("GOOGLE_API_KEY", "").strip()
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN is required.")
    owner_values: list[str] = []
    if owners_csv:
        owner_values.extend([part.strip() for part in owners_csv.split(",") if part.strip()])
    if owner:
        owner_values.append(owner)
    owner_ids: list[int] = []
    for item in owner_values:
        try:
            owner_ids.append(int(item))
        except ValueError as exc:
            raise ValueError(f"Invalid owner id in BOT_OWNER_ID/BOT_OWNER_IDS: {item}") from exc
    owner_ids = list(dict.fromkeys(owner_ids))
    if not owner_ids:
        raise ValueError("BOT_OWNER_ID or BOT_OWNER_IDS is required.")
    return Settings(
        telegram_bot_token=token,
        bot_owner_ids=tuple(owner_ids),
        groq_api_key=groq_key,
        google_api_key=google_key,
        google_model=os.getenv("GOOGLE_MODEL", "gemini-2.0-flash-lite").strip(),
        openrouter_api_key=openrouter_key,
        openrouter_model=os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini").strip(),
        default_timezone=os.getenv("DEFAULT_TIMEZONE", "UTC").strip(),
        global_news_cron=os.getenv("GLOBAL_NEWS_CRON", "0 9 * * *").strip(),
        database_url=os.getenv("DATABASE_URL", "sqlite:///data/bot.db").strip(),
        webhook_url=os.getenv("WEBHOOK_URL", "").strip() or None,
        webhook_secret=os.getenv("WEBHOOK_SECRET_TOKEN", "").strip() or None,
        polling_mode=os.getenv("POLLING_MODE", "true").strip().lower() == "true",
    )
