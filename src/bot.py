from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
from datetime import datetime, timedelta, timezone

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
WATCH_INTERVAL = timedelta(hours=1)
ALERT_COOLDOWN = timedelta(hours=24)
PRICE_MOVE_THRESHOLD_PERCENT = 1.0

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
        BotCommand("headline", "Get top 3 headlines"),
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
        BotCommand("addchannel", "Owner: add channel for scheduled broadcasts"),
        BotCommand("removechannel", "Owner: remove channel from broadcasts"),
        BotCommand("listchannels", "Owner: list configured broadcast channels"),
        BotCommand("sendtest", "Owner: send test broadcast immediately"),
        BotCommand("watchstatus", "Owner: show signal watcher status"),
        BotCommand("forcerunwatch", "Owner: run one signal watcher cycle now"),
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
        google_api_key=settings.google_api_key,
        google_model=settings.google_model,
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

    async def broadcast_news(force_send: bool = False, include_headlines: bool = True) -> int:
        db.purge_expired_users()
        signal_only = not include_headlines
        await news_service.fetch_and_cache_market_snapshot(signal_only=signal_only)
        top_news = await news_service.get_top_news(limit=5, signal_only=signal_only)
        price = await news_service.get_live_price_snapshot()
        headline_message = ""
        if include_headlines:
            headline_context = await news_service.build_headline_context()
            headline_text = await groq_service.curate_headlines(headline_context=headline_context)
            headline_message = _to_html_headlines(headline_text)
        market_context = await news_service.build_market_context(signal_only=signal_only)
        curated = await groq_service.curate_news_update(market_context=market_context)
        sources_html = news_service.build_sources_html(top_news)
        price_html = (
            f"<b>Live Price</b>\n"
            f"XAUUSD: <b>{price['price']}</b> | "
            f"Chg: {price['change']} ({price['change_percent']})\n"
            f"Source: {price['source']}\n"
            f"<a href=\"https://www.tradingview.com/chart/?symbol=TVC%3AGOLD\">Open TradingView GOLD Chart</a>"
        )
        final_message = f"{price_html}\n\n{_to_html_sections(curated)}\n\n{sources_html}"
        recipients = set(settings.bot_owner_ids)
        recipients.update(user.telegram_user_id for user in db.list_authorized_users())
        recipients.update(int(item["channel_id"]) for item in db.list_broadcast_channels())
        sent_count = 0
        for chat_id in recipients:
            try:
                if include_headlines and headline_message:
                    await app.bot.send_message(
                        chat_id=chat_id,
                        text=headline_message,
                        disable_web_page_preview=True,
                        parse_mode="HTML",
                    )
                await app.bot.send_message(
                    chat_id=chat_id,
                    text=final_message,
                    disable_web_page_preview=True,
                    parse_mode="HTML",
                )
                sent_count += 1
            except Exception as exc:
                logger.warning("Failed to broadcast to %s: %s", chat_id, exc)
        if sent_count > 0:
            db.set_last_broadcast_at(_utc_now_iso())
        return sent_count

    scheduler.register_broadcast_callback(lambda: broadcast_news(force_send=False))
    app.bot_data["broadcast_callback"] = broadcast_news
    app.bot_data["watcher_interval_seconds"] = int(WATCH_INTERVAL.total_seconds())
    app.bot_data["watcher_cooldown_seconds"] = int(ALERT_COOLDOWN.total_seconds())

    async def run_signal_watch_cycle(force_send: bool = False) -> dict[str, str | bool]:
        cooldown_seconds = int(app.bot_data.get("watcher_cooldown_seconds", int(ALERT_COOLDOWN.total_seconds())))

        await news_service.fetch_and_cache_market_snapshot(signal_only=True)
        top_news = await news_service.get_top_news(limit=3, signal_only=True)
        market_context = await news_service.build_market_context(signal_only=True)

        curated = await groq_service.curate_news_update(market_context=market_context)
        signal, confidence = _extract_signal_confidence(curated)
        reason = _extract_reason(curated)
        if _is_weak_reason(reason):
            retry_text = await groq_service.curate_news_update(market_context=market_context)
            retry_reason = _extract_reason(retry_text)
            if not _is_weak_reason(retry_reason):
                curated = retry_text
                signal, confidence = _extract_signal_confidence(curated)
                reason = retry_reason

        price_snapshot = await news_service.get_live_price_snapshot()
        current_price = str(price_snapshot.get("price") or "n/a")
        signal, confidence = _apply_signal_validation(
            signal=signal,
            confidence=confidence,
            change_percent=str(price_snapshot.get("change_percent") or ""),
        )
        should_fire = _should_trigger_signal_alert(signal=signal, confidence=confidence)
        headlines_hash = _build_headlines_hash(top_news)
        checked_at = _utc_now_iso()
        watch_state = db.get_watch_state()
        alert_state = db.get_last_alert_state()
        db.set_watch_state(
            checked_at=checked_at,
            signal=signal,
            confidence=confidence,
            price=current_price,
            headlines_hash=headlines_hash,
        )

        if not should_fire or _is_weak_reason(reason):
            return {
                "sent": False,
                "signal": signal,
                "confidence": confidence,
                "decision": "trigger_not_met_or_reason_weak",
            }

        last_sent_signal = str(alert_state.get("signal") or "")
        last_sent_at = str(alert_state.get("sent_at") or "")
        last_price = str(alert_state.get("price") or "")
        last_headlines_hash = str(alert_state.get("headlines_hash") or "")
        signal_flipped = bool(last_sent_signal) and last_sent_signal != signal
        no_previous_alert = not last_sent_at
        material_change = force_send or no_previous_alert or signal_flipped or _price_moved_enough(last_price, current_price) or (
            bool(last_headlines_hash) and last_headlines_hash != headlines_hash
        )
        if not material_change:
            return {
                "sent": False,
                "signal": signal,
                "confidence": confidence,
                "decision": "no_material_change",
            }

        in_cooldown = False
        if last_sent_at and not signal_flipped:
            try:
                last_dt = datetime.fromisoformat(last_sent_at)
                in_cooldown = (datetime.now(timezone.utc) - last_dt).total_seconds() < cooldown_seconds
            except Exception:
                in_cooldown = False
        if in_cooldown and not force_send:
            return {
                "sent": False,
                "signal": signal,
                "confidence": confidence,
                "decision": "cooldown_active",
            }

        sent_count = await broadcast_news(force_send=True, include_headlines=False)
        if sent_count:
            db.set_last_alert_state(
                signal_hash=_build_alert_hash(curated),
                signal=signal,
                confidence=confidence,
                sent_at=_utc_now_iso(),
                price=current_price,
                headlines_hash=headlines_hash,
            )
        return {
            "sent": bool(sent_count),
            "signal": signal,
            "confidence": confidence,
            "decision": "sent" if sent_count else "send_failed",
        }

    app.bot_data["watch_cycle_callback"] = run_signal_watch_cycle

    # User commands
    app.add_handler(CommandHandler("start", user_handlers.start))
    app.add_handler(CommandHandler("help", user_handlers.help_command))
    app.add_handler(CommandHandler("myid", user_handlers.my_id))
    app.add_handler(CommandHandler("headline", user_handlers.headline))
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
    app.add_handler(CommandHandler("addchannel", admin_handlers.add_channel))
    app.add_handler(CommandHandler("removechannel", admin_handlers.remove_channel))
    app.add_handler(CommandHandler("listchannels", admin_handlers.list_channels))
    app.add_handler(CommandHandler("sendtest", admin_handlers.send_test))
    app.add_handler(CommandHandler("watchstatus", admin_handlers.watch_status))
    app.add_handler(CommandHandler("forcerunwatch", admin_handlers.force_run_watch))
    app.add_error_handler(on_error)

    return app


async def run() -> None:
    settings = load_settings()
    app = build_application()
    watcher_task: asyncio.Task | None = None

    async def signal_watcher_loop() -> None:
        run_cycle = app.bot_data["watch_cycle_callback"]
        interval_seconds = int(app.bot_data.get("watcher_interval_seconds", int(WATCH_INTERVAL.total_seconds())))
        while True:
            try:
                result = await run_cycle(force_send=False)
                logger.info(
                    "watcher_tick signal=%s confidence=%s sent=%s decision=%s",
                    str(result.get("signal") or "unknown"),
                    str(result.get("confidence") or "unknown"),
                    bool(result.get("sent")),
                    str(result.get("decision") or "unknown"),
                )
            except Exception as exc:
                logger.warning("Signal watcher tick failed: %s", exc)
            await asyncio.sleep(interval_seconds)

    if settings.polling_mode or not settings.webhook_url:
        logger.info("Starting bot in polling mode.")
        await app.initialize()
        await app.start()
        await register_commands(app)
        watcher_task = asyncio.create_task(signal_watcher_loop(), name="signal_watcher_loop")
        await app.updater.start_polling()
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            if watcher_task:
                watcher_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watcher_task
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

    signal = "BUY"
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

    # Recovery path for malformed AI output: infer from free-text lines.
    if not reason:
        free_text = " ".join(
            line for line in lines if not line.lower().startswith(("signal:", "confidence:", "reason:"))
        ).strip()
        if free_text:
            reason = free_text

    if reason and (
        "showing raw website headlines instead" in reason.lower()
        or "ai provider rejected the request" in reason.lower()
    ):
        reason = "AI analysis is temporarily unavailable, so signal remains conservative until the next successful evaluation."

    if not reason:
        text_blob = " ".join(lines)
        if "bull" in text_blob.lower() or "buy" in text_blob.lower():
            signal = "BUY"
            confidence = "Medium"
            reason = (
                "Price action and headline tone currently favor upside bias; monitor US yield moves and any "
                "fresh geopolitical headlines because a sharp rise in real yields can quickly invalidate this setup."
            )
        elif "bear" in text_blob.lower() or "sell" in text_blob.lower():
            signal = "SELL"
            confidence = "Medium"
            reason = (
                "Recent flows suggest near-term downside pressure in gold; watch for hawkish policy surprises or "
                "USD strength continuation, as either can sustain selling until risk sentiment shifts."
            )
        else:
            signal = "SELL" if any(token in text_blob.lower() for token in ("down", "fall", "drop", "bear")) else "BUY"
            confidence = "Medium"
            reason = (
                "Current inputs are mixed, so directional edge is limited; wait for confirmation from a clean "
                "break in trend, stronger macro catalyst, or a decisive move in yields before taking size."
            )

    if signal == "HOLD":
        signal = _normalize_non_hold_signal(reason)
        if confidence == "Low":
            confidence = "Medium"

    return (
        "<b>Trade Signal</b>\n"
        f"<b>Signal</b>: {escape(_decorate_signal(signal))}\n"
        f"<b>Confidence</b>: {escape(confidence)}\n"
        f"<b>Reason</b>: {escape(reason)}"
    )


def _to_html_headlines(text: str) -> str:
    from html import escape

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    bullets: list[str] = []
    seen: set[str] = set()
    for line in lines:
        clean = line.replace("**", "").replace("__", "").strip()
        if clean.lower().rstrip(":") == "top headlines":
            continue
        payload = clean.lstrip("-• ").strip()
        if not payload:
            continue
        key = " ".join(payload.lower().split())
        if key in seen:
            continue
        seen.add(key)
        bullets.append(f"• {escape(payload)}")
    if not bullets:
        bullets = ["• Headlines unavailable."]
    return "<b>Top Headlines</b>\n" + "\n".join(bullets[:3])


def _extract_signal_confidence(text: str) -> tuple[str, str]:
    signal = ""
    confidence = ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        lower = line.lower()
        if lower.startswith("signal:"):
            signal = line.split(":", 1)[1].strip().upper()
        elif lower.startswith("confidence:"):
            confidence = line.split(":", 1)[1].strip().capitalize()
    if signal == "HOLD":
        signal = _normalize_non_hold_signal(text)
        if not confidence:
            confidence = "Medium"
    return signal, confidence


def _should_trigger_signal_alert(signal: str, confidence: str) -> bool:
    if signal == "BUY" and confidence == "High":
        return True
    if signal == "SELL" and confidence == "High":
        return True
    return False


def _build_alert_hash(curated_text: str) -> str:
    normalized = " ".join(curated_text.strip().lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _build_headlines_hash(news_items: list[dict[str, str | None]]) -> str:
    payload = " || ".join(str(item.get("title") or "").strip().lower() for item in news_items[:3])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest() if payload else ""


def _extract_reason(text: str) -> str:
    for line in text.splitlines():
        clean = line.strip()
        if clean.lower().startswith("reason:"):
            return clean.split(":", 1)[1].strip()
    return ""


def _is_weak_reason(reason: str) -> bool:
    normalized = " ".join(reason.strip().lower().split())
    return not normalized or normalized in {"none", "n/a", "na"}


def _normalize_non_hold_signal(text: str) -> str:
    lower = text.lower()
    bearish = ("down", "drop", "fall", "bear", "selling", "sell", "pressure")
    if any(token in lower for token in bearish):
        return "SELL"
    return "BUY"


def _apply_signal_validation(signal: str, confidence: str, change_percent: str) -> tuple[str, str]:
    pct = _parse_percent(change_percent)
    normalized_signal = signal.upper().strip()
    normalized_conf = confidence.capitalize().strip() if confidence else "Medium"
    if normalized_conf not in {"High", "Medium", "Low"}:
        normalized_conf = "Medium"

    if pct is not None:
        if pct <= -1.0:
            return "SELL", normalized_conf
        if pct >= 1.0:
            return "BUY", normalized_conf
        if normalized_signal not in {"BUY", "SELL"}:
            return ("BUY" if pct >= 0 else "SELL"), "Medium"

    if normalized_signal == "HOLD" or normalized_signal not in {"BUY", "SELL"}:
        return "BUY", "Medium"
    return normalized_signal, normalized_conf


def _parse_percent(value: str) -> float | None:
    clean = value.replace("%", "").replace(",", "").strip()
    try:
        return float(clean)
    except Exception:
        return None


def _price_moved_enough(last_price: str, current_price: str) -> bool:
    try:
        last_val = float(last_price.replace(",", ""))
        current_val = float(current_price.replace(",", ""))
        if last_val == 0:
            return False
        return abs((current_val - last_val) / last_val) * 100 >= PRICE_MOVE_THRESHOLD_PERCENT
    except Exception:
        return False


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decorate_signal(signal: str) -> str:
    mapping = {
        "BUY": "🟢 BUY",
        "SELL": "🔴 SELL",
        "HOLD": "🟡 HOLD",
    }
    return mapping.get(signal, signal)


if __name__ == "__main__":
    asyncio.run(run())
