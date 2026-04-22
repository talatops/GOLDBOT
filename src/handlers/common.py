from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any, Callable

from telegram import Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import ContextTypes

from src.storage.db import Database


def parse_duration(value: str) -> timedelta:
    text = value.strip().lower()
    if text.endswith("d"):
        amount = int(text[:-1])
        return timedelta(days=amount)
    if text.endswith("h"):
        amount = int(text[:-1])
        return timedelta(hours=amount)
    raise ValueError("Duration must end with d or h (example: 7d, 12h).")


def owner_only(handler: Callable[[Update, ContextTypes.DEFAULT_TYPE], Any]):
    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        settings = context.application.bot_data["settings"]
        user = update.effective_user
        if not user or user.id != settings.bot_owner_id:
            if update.effective_message:
                try:
                    await update.effective_message.reply_text("Only the bot owner can use this command.")
                except (NetworkError, TimedOut):
                    return
            return
        return await handler(update, context)

    return wrapper


async def ensure_authorized(
    update: Update, context: ContextTypes.DEFAULT_TYPE, require_owner_override: bool = True
) -> bool:
    db: Database = context.application.bot_data["db"]
    settings = context.application.bot_data["settings"]
    user = update.effective_user
    if not user:
        return False
    if require_owner_override and user.id == settings.bot_owner_id:
        return True
    if db.is_user_authorized(user.id):
        return True
    if update.effective_message:
        try:
            await update.effective_message.reply_text(
                "You do not currently have access. Ask the owner to grant temporary permission."
            )
        except (NetworkError, TimedOut):
            return False
    return False


def expires_at_from_duration(duration: timedelta) -> datetime:
    return datetime.now(timezone.utc) + duration
