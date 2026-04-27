# Telegram Gold News Bot

Python Telegram bot with:
- temporary access permissions managed by owner
- one global scheduled gold market digest
- event-driven signal watcher that can post automatically
- Groq-powered custom Q&A

## Features
- Owner can grant user access for hours or days.
- Authorized users can request latest gold digest using hardcoded sources.
- Owner can configure one global cron schedule for periodic broadcast.
- Bot runs a 10-minute signal watcher and auto-posts when trigger rule matches.
- Authorized users can ask market questions with Groq-generated responses.
- Authorized users can add/remove their own custom RSS/news websites via slash commands.

## Hardcoded Gold Sources
- News feeds:
  - Reuters business RSS
  - Investing commodities RSS
  - Mining.com RSS
- Market reference:
  - TradingView GOLD Chart

Note: TradingView is used as a clickable chart reference in messages. Live numeric price is fetched from API-accessible sources (Yahoo primary, Stooq fallback).

The bot uses website feeds plus Groq curation. If one source fails, it falls back to available sources and cached data.

## Setup
1. Create a virtual environment and install dependencies:
   - `python -m venv .venv`
   - Windows PowerShell: `.venv\\Scripts\\Activate.ps1`
   - `pip install -r requirements.txt`
2. Copy env template:
   - `copy .env.example .env`
3. Fill required values in `.env`.
4. Run:
   - `python -m src.bot`

## Telegram Bot Creation (BotFather)
1. Open Telegram and search for verified `@BotFather`.
2. Send `/newbot`.
3. Enter bot display name.
4. Enter unique username ending with `bot` or `_bot`.
5. Copy the token BotFather gives you.
6. Put it in `.env` as `TELEGRAM_BOT_TOKEN`.
7. Optional checks:
   - Set commands with `/setcommands`.
   - Configure privacy with `/setprivacy` depending on usage.
8. Verify token quickly:
   - Open `https://api.telegram.org/bot<YOUR_TOKEN>/getMe`

## Getting API Keys

### Groq / OpenRouter
1. Create account at [Groq Console](https://console.groq.com/).
2. Create API key.
3. Set `GROQ_API_KEY` in `.env`.
4. Model is auto-selected by the bot from available Groq models.
5. Alternative: use OpenRouter by setting:
   - `OPENROUTER_API_KEY`
   - `OPENROUTER_MODEL` (example: `meta-llama/llama-3.1-8b-instruct:free`)

No separate gold price API key is required in this mode.

## Commands

### Owner-only
- `/adduser <telegram_user_id> <7d|12h> [username]`
- `/removeuser <telegram_user_id>`
- `/listusers`
- `/setschedule <minute> <hour> <day> <month> <day_of_week>`
- `/setdaily <HH:MM>` (UTC)
- `/schedule`
- `/pauseschedule`
- `/resumeschedule`
- `/addchannel <channel_id> [name]`
- `/removechannel <channel_id>`
- `/listchannels`
- `/sendtest`

### Authorized users
- `/headline`
- `/news` (summary window is since last successful broadcast checkpoint)
- `/ask <question>`
- `/addsite <url> [name]`
- `/removesite <url>`
- `/listsites`

## Dynamic Signal Posting (24/7)
- A background watcher checks market/news every 10 minutes.
- Auto-post trigger rule:
  - `BUY + High`, or
  - `SELL + Low/Medium`
- Duplicate suppression:
  - identical alert content is deduped during cooldown window.
- `/sendtest` bypasses trigger checks and sends immediately for verification.

## Free Deployment Options

## 1) Render (easy setup)
- Good for simple deploy workflow from GitHub.
- Limitation: free instance can sleep after inactivity.
- Best with webhook mode:
  - Set `POLLING_MODE=false`
  - Set `WEBHOOK_URL=https://<your-service>.onrender.com`

## 2) fps.ms (bot-focused free hosting)
- Better for near-24/7 hobby bots.
- Limitation: free plan typically requires periodic renewal.

## Recommendation
- Start with Render for easiest deployment.
- If sleep behavior is not acceptable, move to fps.ms.

## Notes
- This bot provides informational summaries only, not financial advice.
- Keep `.env` secret and never commit real keys/tokens.

## VPS Quick Deploy
- Make script executable once:
  - `chmod +x deploy.sh`
- Deploy latest `main` branch and restart service:
  - `./deploy.sh`
- Deploy another branch:
  - `./deploy.sh master`
