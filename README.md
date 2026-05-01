# Telegram Gold Signal Bot

Python Telegram bot with:
- temporary access permissions managed by owner
- one global scheduled signal broadcast
- event-driven contrarian watcher that posts automatically
- deterministic non-AI signal reasoning

## Features
- Owner can grant user access for hours or days.
- Deterministic signal generation from live price math (no AI dependency).
- Owner can configure one global cron schedule for periodic broadcast.
- Bot runs a 30-second watcher and sends contrarian alerts on threshold crosses.
- Authorized users can add/remove their own custom RSS/news websites via slash commands.

## Hardcoded Gold Sources
- News feeds:
  - Reuters business RSS
  - Investing commodities RSS
  - Mining.com RSS
- Market reference:
  - TradingView GOLD Chart

Note: TradingView is used as primary price source via an unofficial scanner endpoint. Stooq is fallback.

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

## Price Source Setup
Set these in `.env`:
- `TRADINGVIEW_SYMBOL` (default `TVC:GOLD`)
- optional `TRADINGVIEW_AUTH_TOKEN` for private/session-backed access
- `SIGNAL_EMA_FAST` and `SIGNAL_EMA_SLOW` for trend filter
- `SIGNAL_ATR_PERIOD` and `SIGNAL_MIN_ATR_PCT` for volatility filter
- `SIGNAL_CONFIRM_TIMEFRAMES` (example `5m,15m`) for confirmation windows

No AI key is required for signal generation in this mode.

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
- `/watchstatus`
- `/forcerunwatch`

### Authorized users
- `/addsite <url> [name]`
- `/removesite <url>`
- `/listsites`

## Dynamic Signal Posting (24/7)
- A background watcher checks price every 30 seconds.
- Contrarian trigger rule:
  - price move `>= +0.05%` from previous check => `BUY` (confidence `High`)
  - price move `<= -0.05%` from previous check => `SELL` (confidence `Medium`)
  - in-band moves do not trigger auto alerts
- Confirmation filters (confirmed-only sends):
  - EMA fast/slow alignment on configured timeframes
  - ATR% above configured minimum
  - structure check not opposing the trigger side
- If base trigger fires but filters fail, alert is skipped and details are visible in `/watchstatus`.
- Cooldown:
  - 24 hours
  - bypassed only when the signal flips side
- `/watchstatus` shows last check, last signal, last sent time, next check, and cooldown remaining.
- `/forcerunwatch` runs one watcher cycle immediately for the owner.

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
