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
from src.services.news_service import NewsService
from src.services.scheduler_service import SchedulerService
from src.storage.db import Database

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
WATCH_INTERVAL = timedelta(seconds=45)
ALERT_COOLDOWN = timedelta(hours=24)
PRICE_MOVE_THRESHOLD_PERCENT = 0.03

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

    news_service = NewsService(
        db=db,
        tradingview_symbol=settings.tradingview_symbol,
        tradingview_auth_token=settings.tradingview_auth_token,
    )
    scheduler = SchedulerService(db=db, timezone_name=settings.default_timezone)

    app = Application.builder().token(settings.telegram_bot_token).build()
    app.bot_data["settings"] = settings
    app.bot_data["db"] = db
    app.bot_data["news_service"] = news_service
    app.bot_data["scheduler"] = scheduler

    async def _broadcast_signal_message(signal: str, confidence: str, reason: str, price: dict[str, str]) -> int:
        db.purge_expired_users()
        price_html = (
            f"<b>Live Price</b>\n"
            f"XAUUSD: <b>{price['price']}</b> | "
            f"Chg: {price['change']} ({price['change_percent']})\n"
            f"Source: {price['source']}\n"
            f"<a href=\"https://www.tradingview.com/chart/?symbol=TVC%3AGOLD\">Open TradingView GOLD Chart</a>"
        )
        final_message = (
            f"{price_html}\n\n"
            "<b>Trade Signal</b>\n"
            f"<b>Signal</b>: {_decorate_signal(signal)}\n"
            f"<b>Confidence</b>: {confidence}\n"
            f"<b>Reason</b>: {reason}"
        )
        recipients = set(settings.bot_owner_ids)
        recipients.update(user.telegram_user_id for user in db.list_authorized_users())
        recipients.update(int(item["channel_id"]) for item in db.list_broadcast_channels())
        sent_count = 0
        for chat_id in recipients:
            try:
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

    async def broadcast_news(force_send: bool = False, include_headlines: bool = True) -> int:
        del include_headlines  # no headline/news mode in no-AI watcher design
        result = await run_signal_watch_cycle(force_send=True if force_send else False)
        return 1 if result.get("sent") else 0

    scheduler.register_broadcast_callback(lambda: broadcast_news(force_send=False))
    app.bot_data["broadcast_callback"] = broadcast_news
    app.bot_data["watcher_interval_seconds"] = int(WATCH_INTERVAL.total_seconds())

    async def run_signal_watch_cycle(force_send: bool = False) -> dict[str, str | bool]:
        price_snapshot = await news_service.get_live_price_snapshot()
        current_price = str(price_snapshot.get("price") or "n/a")
        watch_state = db.get_watch_state()
        prev_price = str(watch_state.get("last_price") or "")
        delta, delta_pct = _compute_delta(prev_price=prev_price, current_price=current_price)
        rule_result, signal, confidence, reason = _deterministic_signal_from_delta(
            delta_percent=delta_pct,
            prev_price=prev_price,
            current_price=current_price,
            previous_signal=str(watch_state.get("last_signal") or ""),
            force_send=force_send,
        )

        diagnostics = await _build_indicator_diagnostics(
            news_service=news_service,
            timeframes=settings.signal_confirm_timeframes,
            ema_fast_period=settings.signal_ema_fast,
            ema_slow_period=settings.signal_ema_slow,
            atr_period=settings.signal_atr_period,
            min_atr_pct=settings.signal_min_atr_pct,
            signal=signal,
        )
        should_fire = rule_result in {"BUY", "SELL"}
        decision_label = "threshold_signal"
        checked_at = _utc_now_iso()
        alert_state = db.get_last_alert_state()
        db.set_watch_state(
            checked_at=checked_at,
            signal=signal,
            confidence=confidence,
            price=current_price,
            prev_price=prev_price,
            delta=f"{delta:.2f}" if delta is not None else "n/a",
            delta_percent=f"{delta_pct:.2f}" if delta_pct is not None else "n/a",
            rule_result=rule_result,
            ema_fast=str(diagnostics.get("ema_fast") or "n/a"),
            ema_slow=str(diagnostics.get("ema_slow") or "n/a"),
            atr=str(diagnostics.get("atr_pct") or "n/a"),
            filter_pass="true" if bool(diagnostics.get("filters_pass")) else "false",
            filter_reason=str(diagnostics.get("filter_reason") or "n/a"),
            timeframe_summary=str(diagnostics.get("timeframes") or "n/a"),
        )

        if not should_fire:
            return {
                "sent": False,
                "signal": signal,
                "confidence": confidence,
                "decision": decision_label,
                "rule_result": rule_result,
                "delta_percent": f"{delta_pct:.4f}" if delta_pct is not None else "n/a",
                "reason": reason,
            }

        # Threshold-only mode: send every cycle.
        del alert_state

        sent_count = await _broadcast_signal_message(signal=signal, confidence=confidence, reason=reason, price=price_snapshot)
        if sent_count:
            db.set_last_alert_state(
                signal_hash=_build_alert_hash(f"{signal}|{confidence}|{reason}|{current_price}|{delta_pct}"),
                signal=signal,
                confidence=confidence,
                sent_at=_utc_now_iso(),
                price=current_price,
                headlines_hash="",
            )
        return {
            "sent": bool(sent_count),
            "signal": signal,
            "confidence": confidence,
            "decision": "sent" if sent_count else "send_failed",
            "rule_result": rule_result,
            "delta_percent": f"{delta_pct:.4f}" if delta_pct is not None else "n/a",
            "reason": reason,
        }

    app.bot_data["watch_cycle_callback"] = run_signal_watch_cycle

    # User commands
    app.add_handler(CommandHandler("start", user_handlers.start))
    app.add_handler(CommandHandler("help", user_handlers.help_command))
    app.add_handler(CommandHandler("myid", user_handlers.my_id))
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
                    "watcher_tick signal=%s confidence=%s sent=%s decision=%s rule=%s delta_pct=%s reason=%s",
                    str(result.get("signal") or "unknown"),
                    str(result.get("confidence") or "unknown"),
                    bool(result.get("sent")),
                    str(result.get("decision") or "unknown"),
                    str(result.get("rule_result") or "unknown"),
                    str(result.get("delta_percent") or "n/a"),
                    str(result.get("reason") or "n/a"),
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


def _compute_delta(prev_price: str, current_price: str) -> tuple[float | None, float | None]:
    try:
        previous = float(prev_price.replace(",", ""))
        current = float(current_price.replace(",", ""))
        if previous == 0:
            return None, None
        delta = current - previous
        delta_pct = (delta / previous) * 100
        return delta, delta_pct
    except Exception:
        return None, None


async def _build_indicator_diagnostics(
    news_service: NewsService,
    timeframes: tuple[str, ...],
    ema_fast_period: int,
    ema_slow_period: int,
    atr_period: int,
    min_atr_pct: float,
    signal: str,
) -> dict[str, str | float | bool]:
    tf_results: list[dict[str, str | float | bool]] = []
    for tf in timeframes:
        candles = await news_service.get_tradingview_ohlc(tf, limit=max(ema_slow_period + 5, atr_period + 5, 40))
        close_vals = [_to_float_or_none(c.get("close")) for c in candles]
        high_vals = [_to_float_or_none(c.get("high")) for c in candles]
        low_vals = [_to_float_or_none(c.get("low")) for c in candles]
        close = [v for v in close_vals if v is not None]
        high = [v for v in high_vals if v is not None]
        low = [v for v in low_vals if v is not None]
        if len(close) < 3 or len(high) < 3 or len(low) < 3:
            latest_close = close[-1] if close else None
            tf_results.append(
                {
                    "timeframe": tf,
                    "ok": False,
                    "reason": "insufficient_candles",
                    "ema_fast": latest_close,
                    "ema_slow": latest_close,
                    "atr_pct": 0.0,
                }
            )
            continue

        fast_period = min(max(2, ema_fast_period), len(close))
        slow_period = min(max(fast_period, ema_slow_period), len(close))
        atr_window = min(max(2, atr_period), max(2, len(close) - 1))

        ema_fast = _ema(close, fast_period)
        ema_slow = _ema(close, slow_period)
        atr = _atr(high, low, close, atr_window)
        latest_close = close[-1]
        atr_pct = (atr / latest_close) * 100 if atr is not None and latest_close else 0.0
        structure = _structure_bias(close[-4:] if len(close) >= 4 else close)

        trend_ok = bool(ema_fast is not None and ema_slow is not None and ((signal == "BUY" and ema_fast > ema_slow) or (signal == "SELL" and ema_fast < ema_slow)))
        atr_ok = bool(atr_pct >= min_atr_pct)
        structure_ok = (signal == "BUY" and structure in {"bullish", "neutral"}) or (signal == "SELL" and structure in {"bearish", "neutral"})
        ok = trend_ok and atr_ok and structure_ok
        reason_parts = []
        if not trend_ok:
            reason_parts.append("ema_trend_mismatch")
        if not atr_ok:
            reason_parts.append("atr_too_low")
        if not structure_ok:
            reason_parts.append("structure_conflict")
        tf_results.append(
            {
                "timeframe": tf,
                "ok": ok,
                "reason": ",".join(reason_parts) if reason_parts else ("ok" if len(close) >= ema_slow_period + 2 else "warmup_ok"),
                "ema_fast": ema_fast,
                "ema_slow": ema_slow,
                "atr_pct": atr_pct,
            }
        )

    valid = [row for row in tf_results if row.get("ema_fast") is not None]
    filters_pass = bool(valid) and all(bool(row.get("ok")) for row in valid)
    primary = valid[0] if valid else {}
    failures = [str(row.get("timeframe")) + ":" + str(row.get("reason")) for row in tf_results if not bool(row.get("ok"))]
    filter_reason = "ok" if filters_pass else (";".join(failures) if failures else "insufficient_indicator_data")
    reason_text = (
        f"Signal withheld: confirmation filters failed ({filter_reason}). "
        f"Need EMA alignment, ATR >= {min_atr_pct:.2f}%, and supportive structure."
    )
    return {
        "filters_pass": filters_pass,
        "filter_reason": filter_reason,
        "reason_text": reason_text,
        "ema_fast": _fmt_opt(primary.get("ema_fast")),
        "ema_slow": _fmt_opt(primary.get("ema_slow")),
        "atr_pct": _fmt_opt(primary.get("atr_pct")),
        "timeframes": ",".join(str(row.get("timeframe")) for row in tf_results),
    }


def _ema(values: list[float], period: int) -> float | None:
    if len(values) < period or period <= 1:
        return None
    k = 2 / (period + 1)
    ema_val = sum(values[:period]) / period
    for price in values[period:]:
        ema_val = (price * k) + (ema_val * (1 - k))
    return ema_val


def _atr(high: list[float], low: list[float], close: list[float], period: int) -> float | None:
    if len(high) != len(low) or len(low) != len(close) or len(close) < period + 1:
        return None
    trs: list[float] = []
    for i in range(1, len(close)):
        tr = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
        trs.append(tr)
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def _structure_bias(values: list[float]) -> str:
    if len(values) < 3:
        return "neutral"
    if values[-1] > values[-2] > values[-3]:
        return "bullish"
    if values[-1] < values[-2] < values[-3]:
        return "bearish"
    return "neutral"


def _to_float_or_none(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _fmt_opt(value: object) -> str:
    maybe = _to_float_or_none(value)
    return f"{maybe:.4f}" if maybe is not None else "n/a"


def _deterministic_signal_from_delta(
    delta_percent: float | None,
    prev_price: str,
    current_price: str,
    previous_signal: str,
    force_send: bool,
) -> tuple[str, str, str, str]:
    del force_send
    if delta_percent is not None:
        if delta_percent >= PRICE_MOVE_THRESHOLD_PERCENT:
            reason = (
                f"Signal engine detected strengthening bullish momentum as price moved from {prev_price or 'n/a'} to {current_price} "
                f"({delta_percent:.2f}%). BUY bias is active while upside pressure remains dominant."
            )
            return "BUY", "BUY", "High", reason
        if delta_percent <= -PRICE_MOVE_THRESHOLD_PERCENT:
            reason = (
                f"Signal engine detected increasing bearish momentum as price moved from {prev_price or 'n/a'} to {current_price} "
                f"({delta_percent:.2f}%). SELL bias is active while downside pressure remains dominant."
            )
            return "SELL", "SELL", "Medium", reason
        signal = previous_signal.upper().strip() if previous_signal.upper().strip() in {"BUY", "SELL"} else "BUY"
        confidence = "High" if signal == "BUY" else "Medium"
        reason = (
            f"Price moved {delta_percent:.2f}% from {prev_price or 'n/a'} to {current_price}, "
            "and momentum remains mixed, so no fresh directional signal is issued yet."
        )
        return "NO_SIGNAL", signal, confidence, reason

    signal = "BUY"
    confidence = "High"
    reason = "Previous comparison price is unavailable, so no fresh momentum signal is generated until two consecutive prices are available."
    return "NO_SIGNAL", signal, confidence, reason


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
