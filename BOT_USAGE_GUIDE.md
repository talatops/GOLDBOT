# Bot Usage Guide (Owner vs Temporary User)

This is a simple reference for who can run which command and what each command does.

## OWNER Commands

Use these commands only from the Telegram account configured as bot owner.

- `/adduser <telegram_user_id> <7d|12h> [username]`  
  Grants temporary access to a user for the specified duration.
  Example: `/adduser 987654321 7d ali`

- `/removeuser <telegram_user_id>`  
  Revokes a user's access immediately.
  Example: `/removeuser 987654321`

- `/listusers`  
  Shows all currently authorized temporary users and access details.
  Example: `/listusers`

- `/setschedule <minute> <hour> <day> <month> <day_of_week>`  
  Sets bot digest schedule using cron format.
  Example: `/setschedule 0 9 * * *`

- `/setdaily <HH:MM>`  
  Sets a daily digest time (UTC), easier than full cron.
  Example: `/setdaily 09:00`

- `/schedule`  
  Displays the currently configured schedule.
  Example: `/schedule`

- `/pauseschedule`  
  Temporarily stops scheduled digest messages.
  Example: `/pauseschedule`

- `/resumeschedule`  
  Restarts scheduled digest messages after pause.
  Example: `/resumeschedule`

- `/addchannel <channel_id> [name]`  
  Adds a Telegram channel to scheduled and signal-triggered broadcasts.
  Example: `/addchannel -1001234567890 GoldSignals`

- `/removechannel <channel_id>`  
  Removes a channel from broadcast recipients.
  Example: `/removechannel -1001234567890`

- `/listchannels`  
  Lists all configured broadcast channels.
  Example: `/listchannels`

- `/sendtest`  
  Sends an immediate test signal broadcast to owner/users/channels.
  Example: `/sendtest`

- `/watchstatus`  
  Shows watcher health and state such as last check, last signal, last sent alert, next check, and cooldown.
  Example: `/watchstatus`

- `/forcerunwatch`  
  Forces one watcher cycle immediately without waiting for the next hourly run.
  Example: `/forcerunwatch`

## TEMPORARY USER Commands

Use these after owner has granted access via `/adduser`.

- `/addsite <url> [name]`  
  Adds a personal custom news source for your own digest.
  Example: `/addsite https://example.com/rss MySource`

- `/removesite <url>`  
  Removes one of your previously added custom sources.
  Example: `/removesite https://example.com/rss`

- `/listsites`  
  Lists your custom saved news sources.
  Example: `/listsites`

## Quick Role Summary

- **OWNER**: manages users, channels, scheduling, and can force-test broadcasts.
- **TEMPORARY USER**: reads news, asks questions, and manages own custom sources.

## Dynamic Signal Posting (Always-On)

- Bot watcher runs every 30 seconds in polling mode.
- Contrarian auto-post rule:
  - if price move from previous check is `>= +0.05%` -> `BUY`
  - if price move from previous check is `<= -0.05%` -> `SELL`
  - if move is within (-0.05%, +0.05%), no auto alert is sent
- Confirmation filters are required before send:
  - EMA fast/slow trend alignment on configured confirmation timeframes
  - ATR% above minimum threshold
  - structure check does not conflict with signal direction
- If trigger side is valid but filters fail, the bot skips alert and records reasons in `/watchstatus`.
- Normal cooldown is 24 hours unless the signal flips side.
- Reason text is deterministic and generated from price math variables.
