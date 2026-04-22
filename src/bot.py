from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram import BotCommand
from telegram.ext import Application, CommandHandler

from src.config import load_settings
from src.handlers import admin as admin_handlers
from src.handlers import user as user_handlers
from src.services.groq_service import GroqService
from src.services.news_service import NewsService
from src.services.scheduler_service import SchedulerService
from src.storage.db import Database

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

async def on_error(update: object, context) -> None:
    update_id = None
    if isinstance(update, Update) and update.update_id is not None:
        update_id = update.update_id
    logger.warning("Handled Telegram error for update_id=%s: %s", update_id, context.error)


async def register_commands(app: Application) -> None:
    commands = [
        BotCommand("start", "Intro"),
        BotCommand("help", "Show help and command list"),
        BotCommand("myid", "Show your Telegram user ID"),
        BotCommand("news", "Get latest gold market brief"),
        BotCommand("ask", "Ask a custom market question"),
        BotCommand("addsite", "Add custom RSS/news website"),
        BotCommand("removesite", "Remove custom website"),
        BotCommand("listsites", "List your custom websites"),
        BotCommand("adduser", "Owner: grant user temporary access"),
        BotCommand("removeuser", "Owner: revoke user access"),
        BotCommand("listusers", "Owner: list authorized users"),
        BotCommand("setschedule", "Owner: set cron schedule"),
        BotCommand("setdaily", "Owner: set daily UTC time"),
        BotCommand("schedule", "Owner: show schedule status"),
        BotCommand("pauseschedule", "Owner: pause scheduled sends"),
        BotCommand("resumeschedule", "Owner: resume scheduled sends"),
        BotCommand("broadcastnow", "Owner: send broadcast now"),
    ]
    try:
        await app.bot.set_my_commands(commands)
    except Exception as exc:
        logger.warning("Could not register bot commands: %s", exc)


def build_application() -> Application:
    settings = load_settings()
    db = Database(settings.database_path)
    db.set_setting("global_news_cron", db.get_setting("global_news_cron", settings.global_news_cron) or settings.global_news_cron)

    news_service = NewsService(db=db)
    groq_service = GroqService(
        api_key=settings.groq_api_key,
        openrouter_api_key=settings.openrouter_api_key,
        openrouter_model=settings.openrouter_model,
    )
    scheduler = SchedulerService(db=db, timezone_name=settings.default_timezone)

    app = Application.builder().token(settings.telegram_bot_token).build()
    app.bot_data["settings"] = settings
    app.bot_data["db"] = db
    app.bot_data["news_service"] = news_service
    app.bot_data["groq_service"] = groq_service
    app.bot_data["scheduler"] = scheduler

    async def broadcast_news() -> None:
        db.purge_expired_users()
        await news_service.fetch_and_cache_market_snapshot()
        top_news = await news_service.get_top_news(limit=5)
        price = await news_service.get_live_price_snapshot()
        market_context = await news_service.build_market_context()
        curated = await groq_service.curate_news_update(market_context=market_context)
        sources_html = news_service.build_sources_html(top_news)
        price_html = (
            f"<b>Live Price</b>\n"
            f"XAUUSD: <b>{price['price']}</b> | "
            f"Chg: {price['change']} ({price['change_percent']})\n"
            f"Source: {price['source']}\n"
            f"<a href=\"https://www.tradingview.com/chart/?symbol=TVC%3AGOLD\">Open TradingView GOLD Chart</a>"
        )
        if str(price.get("fallback_active", "false")).lower() == "true":
            price_html += "\n<b>Note</b>: fallback source active"
        final_message = f"{price_html}\n\n{_to_html_sections(curated)}\n\n{sources_html}"
        recipients = {settings.bot_owner_id}
        recipients.update(user.telegram_user_id for user in db.list_authorized_users())
        for chat_id in recipients:
            try:
                await app.bot.send_message(
                    chat_id=chat_id,
                    text=final_message,
                    disable_web_page_preview=True,
                    parse_mode="HTML",
                )
            except Exception as exc:
                logger.warning("Failed to broadcast to %s: %s", chat_id, exc)

    scheduler.register_broadcast_callback(broadcast_news)

    # User commands
    app.add_handler(CommandHandler("start", user_handlers.start))
    app.add_handler(CommandHandler("help", user_handlers.help_command))
    app.add_handler(CommandHandler("myid", user_handlers.my_id))
    app.add_handler(CommandHandler("news", user_handlers.news))
    app.add_handler(CommandHandler("ask", user_handlers.ask))
    app.add_handler(CommandHandler("addsite", user_handlers.add_site))
    app.add_handler(CommandHandler("removesite", user_handlers.remove_site))
    app.add_handler(CommandHandler("listsites", user_handlers.list_sites))

    # Owner commands
    app.add_handler(CommandHandler("adduser", admin_handlers.add_user))
    app.add_handler(CommandHandler("removeuser", admin_handlers.remove_user))
    app.add_handler(CommandHandler("listusers", admin_handlers.list_users))
    app.add_handler(CommandHandler("setschedule", admin_handlers.set_schedule))
    app.add_handler(CommandHandler("setdaily", admin_handlers.set_daily))
    app.add_handler(CommandHandler("schedule", admin_handlers.schedule_status))
    app.add_handler(CommandHandler("pauseschedule", admin_handlers.pause_schedule))
    app.add_handler(CommandHandler("resumeschedule", admin_handlers.resume_schedule))
    app.add_handler(CommandHandler("broadcastnow", admin_handlers.broadcast_now))
    app.add_error_handler(on_error)

    return app


async def run() -> None:
    settings = load_settings()
    app = build_application()

    if settings.polling_mode or not settings.webhook_url:
        logger.info("Starting bot in polling mode.")
        await app.initialize()
        await app.start()
        await register_commands(app)
        await app.updater.start_polling()
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
        return

    logger.info("Starting bot in webhook mode.")
    await app.run_webhook(
        listen="0.0.0.0",
        port=8000,
        webhook_url=settings.webhook_url,
        secret_token=settings.webhook_secret,
    )


def _to_html_sections(text: str) -> str:
    from html import escape

    lines = [line.strip().replace("**", "").replace("__", "") for line in text.splitlines() if line.strip()]
    if not lines:
        return "<b>Trade Signal</b>\nSignal: HOLD\nConfidence: Low\nReason: No generated analysis available."

    signal = "HOLD"
    confidence = "Low"
    reason = ""

    for raw in lines:
        lower = raw.lower()
        if lower.startswith("signal:"):
            value = raw.split(":", 1)[1].strip().upper()
            if value in {"BUY", "SELL", "HOLD"}:
                signal = value
            continue
        if lower.startswith("confidence:"):
            value = raw.split(":", 1)[1].strip().capitalize()
            if value in {"High", "Medium", "Low"}:
                confidence = value
            continue
        if lower.startswith("reason:"):
            reason = raw.split(":", 1)[1].strip()
            continue

    if not reason:
        text_blob = " ".join(lines)
        if "bull" in text_blob.lower() or "buy" in text_blob.lower():
            signal = "BUY"
            confidence = "Medium"
            reason = "Positive momentum signals are currently stronger than bearish cues."
        elif "bear" in text_blob.lower() or "sell" in text_blob.lower():
            signal = "SELL"
            confidence = "Medium"
            reason = "Downside risk factors currently outweigh bullish momentum cues."
        else:
            signal = "HOLD"
            confidence = "Low"
            reason = "Mixed signals with no clear directional edge from current inputs."

    return (
        "<b>Trade Signal</b>\n"
        f"<b>Signal</b>: {escape(signal)}\n"
        f"<b>Confidence</b>: {escape(confidence)}\n"
        f"<b>Reason</b>: {escape(reason)}"
    )


if __name__ == "__main__":
    asyncio.run(run())
