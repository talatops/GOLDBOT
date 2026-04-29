from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.handlers.common import expires_at_from_duration, owner_only, parse_duration
from src.services.scheduler_service import daily_to_cron
from src.storage.db import Database

from telegram import Update
from telegram.ext import ContextTypes


@owner_only
async def add_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.effective_message.reply_text("Usage: /adduser <telegram_user_id> <duration e.g. 7d or 12h>")
        return
    db: Database = context.application.bot_data["db"]
    actor_id = update.effective_user.id
    try:
        user_id = int(context.args[0])
        duration = parse_duration(context.args[1])
    except ValueError as exc:
        await update.effective_message.reply_text(f"Invalid input: {exc}")
        return
    username = context.args[2] if len(context.args) > 2 else None
    expires_at = expires_at_from_duration(duration)
    db.add_or_extend_user(user_id, username, expires_at, actor_id)
    db.add_audit_log(actor_id, "add_user", f"user_id={user_id}, expires_at={expires_at.isoformat()}")
    await update.effective_message.reply_text(f"User {user_id} authorized until {expires_at.isoformat()}.")


@owner_only
async def remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text("Usage: /removeuser <telegram_user_id>")
        return
    db: Database = context.application.bot_data["db"]
    actor_id = update.effective_user.id
    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("telegram_user_id must be an integer.")
        return
    db.remove_user(user_id)
    db.add_audit_log(actor_id, "remove_user", f"user_id={user_id}")
    await update.effective_message.reply_text(f"User {user_id} removed from authorized users.")


@owner_only
async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.application.bot_data["db"]
    users = db.list_authorized_users()
    if not users:
        await update.effective_message.reply_text("No authorized users currently.")
        return
    lines = ["Authorized users:"]
    for u in users:
        lines.append(f"- {u.telegram_user_id} ({u.username or 'no username'}) expires {u.expires_at.isoformat()}")
    await update.effective_message.reply_text("\n".join(lines))


@owner_only
async def set_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 5:
        await update.effective_message.reply_text("Usage: /setschedule <min> <hour> <day> <month> <dow>")
        return
    scheduler = context.application.bot_data["scheduler"]
    db: Database = context.application.bot_data["db"]
    actor_id = update.effective_user.id
    cron_expr = " ".join(context.args)
    try:
        scheduler.set_schedule(cron_expr)
    except ValueError as exc:
        await update.effective_message.reply_text(f"Invalid cron: {exc}")
        return
    db.add_audit_log(actor_id, "set_schedule", cron_expr)
    await update.effective_message.reply_text(f"Global schedule set to `{cron_expr}`.", parse_mode="Markdown")


@owner_only
async def set_daily(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text("Usage: /setdaily <HH:MM>")
        return
    scheduler = context.application.bot_data["scheduler"]
    db: Database = context.application.bot_data["db"]
    actor_id = update.effective_user.id
    try:
        cron_expr = daily_to_cron(context.args[0])
        scheduler.set_schedule(cron_expr)
    except ValueError as exc:
        await update.effective_message.reply_text(f"Invalid time: {exc}")
        return
    db.add_audit_log(actor_id, "set_daily", cron_expr)
    await update.effective_message.reply_text(f"Daily global news schedule set to `{context.args[0]}` UTC.", parse_mode="Markdown")


@owner_only
async def schedule_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    scheduler = context.application.bot_data["scheduler"]
    expr, paused = scheduler.get_schedule()
    if not expr:
        await update.effective_message.reply_text("No schedule configured.")
        return
    await update.effective_message.reply_text(f"Cron: `{expr}` | paused: `{paused}`", parse_mode="Markdown")


@owner_only
async def pause_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    scheduler = context.application.bot_data["scheduler"]
    db: Database = context.application.bot_data["db"]
    actor_id = update.effective_user.id
    scheduler.pause_schedule()
    db.add_audit_log(actor_id, "pause_schedule")
    await update.effective_message.reply_text("Global schedule paused.")


@owner_only
async def resume_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    scheduler = context.application.bot_data["scheduler"]
    db: Database = context.application.bot_data["db"]
    actor_id = update.effective_user.id
    scheduler.resume_schedule()
    db.add_audit_log(actor_id, "resume_schedule")
    await update.effective_message.reply_text("Global schedule resumed.")


@owner_only
async def add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text("Usage: /addchannel <channel_id> [channel_name]")
        return
    db: Database = context.application.bot_data["db"]
    actor_id = update.effective_user.id
    try:
        channel_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("channel_id must be an integer (usually starts with -100).")
        return
    channel_name = " ".join(context.args[1:]).strip() if len(context.args) > 1 else None
    db.add_broadcast_channel(channel_id=channel_id, channel_name=channel_name, added_by=actor_id)
    db.add_audit_log(actor_id, "add_channel", f"channel_id={channel_id}")
    await update.effective_message.reply_text(
        "Channel saved. Make sure this bot is added to that channel as an admin to send scheduled updates."
    )


@owner_only
async def remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text("Usage: /removechannel <channel_id>")
        return
    db: Database = context.application.bot_data["db"]
    actor_id = update.effective_user.id
    try:
        channel_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("channel_id must be an integer (usually starts with -100).")
        return
    removed = db.remove_broadcast_channel(channel_id=channel_id)
    if not removed:
        await update.effective_message.reply_text("Channel was not in broadcast list.")
        return
    db.add_audit_log(actor_id, "remove_channel", f"channel_id={channel_id}")
    await update.effective_message.reply_text(f"Channel {channel_id} removed from scheduled broadcast.")


@owner_only
async def list_channels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.application.bot_data["db"]
    channels = db.list_broadcast_channels()
    if not channels:
        await update.effective_message.reply_text("No broadcast channels configured yet.")
        return
    lines = ["Broadcast channels:"]
    for row in channels:
        cid = row.get("channel_id")
        cname = row.get("channel_name") or "no name"
        lines.append(f"- {cid} ({cname})")
    await update.effective_message.reply_text("\n".join(lines))


@owner_only
async def send_test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    callback = context.application.bot_data.get("broadcast_callback")
    if callback is None:
        await update.effective_message.reply_text("Broadcast callback is not ready yet. Try again in a few seconds.")
        return
    await update.effective_message.reply_text("Running test broadcast now...")
    try:
        await callback(force_send=True)
    except TypeError:
        await callback()
    await update.effective_message.reply_text("Test broadcast sent to owner, authorized users, and configured channels.")


@owner_only
async def watch_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.application.bot_data["db"]
    interval_seconds = int(context.application.bot_data.get("watcher_interval_seconds", 3600))
    cooldown_seconds = int(context.application.bot_data.get("watcher_cooldown_seconds", 86400))
    watch_state = db.get_watch_state()
    last_checked = watch_state.get("last_checked_at") or "not checked yet"
    last_signal = watch_state.get("last_signal") or "not available yet"
    last_confidence = watch_state.get("last_confidence") or "not available yet"
    last_price = watch_state.get("last_price") or "n/a"
    last_sent = watch_state.get("last_sent_at") or "never"
    next_check = "not scheduled yet"
    cooldown_remaining = "0s"

    if watch_state.get("last_checked_at"):
        try:
            checked_dt = datetime.fromisoformat(str(watch_state["last_checked_at"]))
            next_check = (checked_dt + timedelta(seconds=interval_seconds)).isoformat()
        except Exception:
            next_check = "not scheduled yet"

    if watch_state.get("last_sent_at"):
        try:
            sent_dt = datetime.fromisoformat(str(watch_state["last_sent_at"]))
            remaining = sent_dt + timedelta(seconds=cooldown_seconds) - datetime.now(timezone.utc)
            cooldown_remaining = "0s" if remaining.total_seconds() <= 0 else str(remaining).split(".")[0]
        except Exception:
            cooldown_remaining = "unknown"

    lines = [
        "Watcher status:",
        f"- Last checked: {last_checked}",
        f"- Last signal: {last_signal} ({last_confidence})",
        f"- Last price: {last_price}",
        f"- Last sent alert: {last_sent}",
        f"- Next check: {next_check}",
        f"- Cooldown remaining: {cooldown_remaining}",
    ]
    await update.effective_message.reply_text("\n".join(lines))


@owner_only
async def force_run_watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    callback = context.application.bot_data.get("watch_cycle_callback")
    if callback is None:
        await update.effective_message.reply_text("Signal watcher is not ready yet. Try again in a few seconds.")
        return
    await update.effective_message.reply_text("Running watcher cycle now...")
    result = await callback(force_send=True)
    await update.effective_message.reply_text(
        f"Watcher cycle complete.\n"
        f"Signal: {result.get('signal', 'unknown')}\n"
        f"Confidence: {result.get('confidence', 'unknown')}\n"
        f"Sent: {result.get('sent', False)}\n"
        f"Decision: {result.get('decision', 'unknown')}"
    )

