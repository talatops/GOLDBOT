from __future__ import annotations

from collections.abc import Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.storage.db import Database


class SchedulerService:
    JOB_ID = "global_news_broadcast"

    def __init__(self, db: Database, timezone_name: str) -> None:
        self._db = db
        self._timezone = timezone_name
        self._scheduler = AsyncIOScheduler(timezone=timezone_name)
        self._scheduler.start()
        self._broadcast_fn: Callable[[], Awaitable[None]] | None = None

    def register_broadcast_callback(self, callback: Callable[[], Awaitable[None]]) -> None:
        self._broadcast_fn = callback
        self.apply_saved_schedule()

    def apply_saved_schedule(self) -> None:
        cron_expr = self._db.get_setting("global_news_cron")
        paused = self._db.get_setting("global_news_paused", "false") == "true"
        if not cron_expr or not self._broadcast_fn:
            return
        self._set_job(cron_expr)
        if paused:
            self.pause_schedule()

    def set_schedule(self, cron_expr: str) -> None:
        self._db.set_setting("global_news_cron", cron_expr)
        self._db.set_setting("global_news_paused", "false")
        self._set_job(cron_expr)

    def _set_job(self, cron_expr: str) -> None:
        if not self._broadcast_fn:
            return
        trigger = cron_trigger_from_expression(cron_expr, self._timezone)
        self._scheduler.add_job(
            self._broadcast_fn,
            trigger=trigger,
            id=self.JOB_ID,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=120,
        )

    def get_schedule(self) -> tuple[str | None, bool]:
        expr = self._db.get_setting("global_news_cron")
        paused = self._db.get_setting("global_news_paused", "false") == "true"
        return expr, paused

    def pause_schedule(self) -> None:
        try:
            self._scheduler.pause_job(self.JOB_ID)
        except Exception:
            pass
        self._db.set_setting("global_news_paused", "true")

    def resume_schedule(self) -> None:
        try:
            self._scheduler.resume_job(self.JOB_ID)
        except Exception:
            pass
        self._db.set_setting("global_news_paused", "false")

    async def trigger_now(self) -> None:
        if self._broadcast_fn:
            await self._broadcast_fn()


def cron_trigger_from_expression(cron_expr: str, timezone_name: str) -> CronTrigger:
    fields = cron_expr.split()
    if len(fields) != 5:
        raise ValueError("Cron expression must have 5 fields: minute hour day month day_of_week.")
    minute, hour, day, month, day_of_week = fields
    return CronTrigger(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=day_of_week,
        timezone=timezone_name,
    )


def daily_to_cron(hhmm: str) -> str:
    if ":" not in hhmm:
        raise ValueError("Time must be HH:MM.")
    hour_s, minute_s = hhmm.split(":", maxsplit=1)
    hour = int(hour_s)
    minute = int(minute_s)
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("Time must be valid 24-hour format HH:MM.")
    return f"{minute} {hour} * * *"
