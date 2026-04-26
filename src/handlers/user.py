from __future__ import annotations

from html import escape

from telegram import Update
from telegram.ext import ContextTypes

from src.handlers.common import ensure_authorized
from src.services.groq_service import GroqService
from src.services.news_service import NewsService


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = (
        "Welcome to Gold News Bot.\n"
        "Use /help to see commands.\n"
        "Access to /news and /ask requires temporary permission from the bot owner."
    )
    await update.effective_message.reply_text(message)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "User commands:\n"
        "/start - Intro\n"
        "/help - This help\n"
        "/myid - Show your Telegram user ID\n"
        "/news - Latest gold digest\n"
        "/headline - Top 3 improved headlines\n"
        "/ask <question> - Ask a custom market question\n\n"
        "/addsite <url> [name] - Add your custom RSS/news site\n"
        "/removesite <url> - Remove your custom site\n"
        "/listsites - List your custom sites\n\n"
        "Owner commands:\n"
        "/adduser <id> <7d|12h> [username]\n"
        "/removeuser <id>\n"
        "/listusers\n"
        "/setschedule <m h dom mon dow>\n"
        "/setdaily <HH:MM>\n"
        "/schedule\n"
        "/pauseschedule\n"
        "/resumeschedule"
    )
    await update.effective_message.reply_text(text)


async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    await update.effective_message.reply_text(f"Your Telegram user ID is: `{user.id}`", parse_mode="Markdown")


async def news(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_authorized(update, context):
        return
    user = update.effective_user
    news_service: NewsService = context.application.bot_data["news_service"]
    groq_service: GroqService = context.application.bot_data["groq_service"]
    await news_service.fetch_and_cache_market_snapshot(owner_user_id=user.id if user else None)
    top_news = await news_service.get_top_news(owner_user_id=user.id if user else None, limit=3)
    price = await news_service.get_live_price_snapshot()
    market_context = await news_service.build_market_context(owner_user_id=user.id if user else None)
    curated = await groq_service.curate_news_update(market_context=market_context)
    formatted_curated = _to_html_sections(curated)
    sources_html = news_service.build_sources_html(top_news)
    price_html = (
        f"<b>Live Price</b>\n"
        f"XAUUSD: <b>{escape(price['price'])}</b> | "
        f"Chg: {escape(price['change'])} ({escape(price['change_percent'])})\n"
        f"Source: {escape(price['source'])}\n"
        f"<a href=\"https://www.tradingview.com/chart/?symbol=TVC%3AGOLD\">Open TradingView GOLD Chart</a>"
    )
    final_message = f"{price_html}\n\n{formatted_curated}\n\n{sources_html}"
    await update.effective_message.reply_text(
        final_message,
        disable_web_page_preview=True,
        parse_mode="HTML",
    )


async def headline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_authorized(update, context):
        return
    user = update.effective_user
    news_service: NewsService = context.application.bot_data["news_service"]
    groq_service: GroqService = context.application.bot_data["groq_service"]
    await news_service.fetch_and_cache_market_snapshot(owner_user_id=user.id if user else None)
    headline_context = await news_service.build_headline_context(owner_user_id=user.id if user else None)
    improved = await groq_service.curate_headlines(headline_context=headline_context)
    await update.effective_message.reply_text(
        _to_html_headline(improved),
        disable_web_page_preview=True,
        parse_mode="HTML",
    )


async def ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_authorized(update, context):
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /ask <your question>")
        return

    question = " ".join(context.args)
    user = update.effective_user
    news_service: NewsService = context.application.bot_data["news_service"]
    groq_service: GroqService = context.application.bot_data["groq_service"]

    await news_service.fetch_and_cache_market_snapshot(owner_user_id=user.id if user else None)
    market_context = await news_service.build_question_context(
        owner_user_id=user.id if user else None,
        question=question,
    )
    answer = await groq_service.answer(question=question, market_context=market_context)
    await update.effective_message.reply_text(
        _to_html_answer(answer),
        disable_web_page_preview=True,
        parse_mode="HTML",
    )


async def add_site(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_authorized(update, context):
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /addsite <url> [source_name]")
        return

    user = update.effective_user
    if not user:
        return

    source_url = context.args[0].strip()
    if not (source_url.startswith("http://") or source_url.startswith("https://")):
        await update.effective_message.reply_text("URL must start with http:// or https://")
        return
    source_name = " ".join(context.args[1:]).strip() if len(context.args) > 1 else None

    db = context.application.bot_data["db"]
    db.add_custom_source(owner_user_id=user.id, source_url=source_url, source_name=source_name)
    db.add_audit_log(user.id, "add_site", f"url={source_url}")
    await update.effective_message.reply_text("Custom source saved. It will be used in /news and /ask.")


async def remove_site(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_authorized(update, context):
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /removesite <url>")
        return

    user = update.effective_user
    if not user:
        return
    source_url = context.args[0].strip()

    db = context.application.bot_data["db"]
    removed = db.remove_custom_source(owner_user_id=user.id, source_url=source_url)
    if removed:
        db.add_audit_log(user.id, "remove_site", f"url={source_url}")
        await update.effective_message.reply_text("Custom source removed.")
        return
    await update.effective_message.reply_text("Source not found for your account.")


async def list_sites(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_authorized(update, context):
        return
    user = update.effective_user
    if not user:
        return

    db = context.application.bot_data["db"]
    sources = db.list_custom_sources(owner_user_id=user.id)
    if not sources:
        await update.effective_message.reply_text("You have no custom sources yet.")
        return

    lines = ["Your custom sources:"]
    for item in sources:
        lines.append(f"- {item.get('source_name') or 'CustomSource'}: {item.get('source_url')}")
    await update.effective_message.reply_text("\n".join(lines))


def _to_html_sections(text: str) -> str:
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

    # Recovery path for malformed AI output: infer from free-text lines.
    if not reason:
        free_text = " ".join(
            line for line in lines if not line.lower().startswith(("signal:", "confidence:", "reason:"))
        ).strip()
        if free_text:
            reason = free_text

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
            signal = "HOLD"
            confidence = "Low"
            reason = (
                "Current inputs are mixed, so directional edge is limited; wait for confirmation from a clean "
                "break in trend, stronger macro catalyst, or a decisive move in yields before taking size."
            )

    return (
        "<b>Trade Signal</b>\n"
        f"<b>Signal</b>: {escape(signal)}\n"
        f"<b>Confidence</b>: {escape(confidence)}\n"
        f"<b>Reason</b>: {escape(reason)}"
    )


def _to_html_answer(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return "<b>Answer</b>\nNo result."
    return "<b>Answer</b>\n" + "\n".join(escape(line.replace("**", "").replace("__", "")) for line in lines)


def _to_html_headline(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    bullets = []
    seen: set[str] = set()
    for line in lines:
        clean = line.replace("**", "").replace("__", "").strip()
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
