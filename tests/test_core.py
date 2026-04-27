from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.bot import _extract_signal_confidence, _should_trigger_signal_alert
from src.handlers.common import parse_duration
from src.services.scheduler_service import cron_trigger_from_expression, daily_to_cron
from src.storage.db import Database


def test_parse_duration_days_and_hours() -> None:
    assert parse_duration("7d") == timedelta(days=7)
    assert parse_duration("12h") == timedelta(hours=12)


def test_parse_duration_invalid() -> None:
    try:
        parse_duration("10m")
    except ValueError:
        assert True
    else:
        assert False, "Expected ValueError"


def test_daily_to_cron() -> None:
    assert daily_to_cron("09:30") == "30 9 * * *"


def test_cron_expression_validation() -> None:
    cron_trigger_from_expression("0 9 * * *", "UTC")
    try:
        cron_trigger_from_expression("0 9 * *", "UTC")
    except ValueError:
        assert True
    else:
        assert False, "Expected ValueError for invalid cron"


def test_db_authorization_and_purge(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    db.add_or_extend_user(12345, "testuser", future, 1)
    assert db.is_user_authorized(12345) is True

    past = datetime.now(timezone.utc) - timedelta(hours=1)
    db.add_or_extend_user(12345, "testuser", past, 1)
    db.purge_expired_users()
    assert db.is_user_authorized(12345) is False


def test_custom_sources_lifecycle(tmp_path: Path) -> None:
    db = Database(tmp_path / "sources.db")
    db.add_custom_source(owner_user_id=101, source_url="https://example.com/feed.xml", source_name="ExampleFeed")
    db.add_custom_source(owner_user_id=101, source_url="https://example.org/rss", source_name=None)
    mine = db.list_custom_sources(owner_user_id=101)
    assert len(mine) == 2
    assert any(str(item.get("source_url")) == "https://example.com/feed.xml" for item in mine)

    removed = db.remove_custom_source(owner_user_id=101, source_url="https://example.com/feed.xml")
    assert removed is True
    mine_after = db.list_custom_sources(owner_user_id=101)
    assert len(mine_after) == 1


def test_broadcast_channels_lifecycle(tmp_path: Path) -> None:
    db = Database(tmp_path / "channels.db")
    db.add_broadcast_channel(channel_id=-1001234567890, added_by=999, channel_name="Gold Alerts")
    db.add_broadcast_channel(channel_id=-1009876543210, added_by=999, channel_name=None)
    channels = db.list_broadcast_channels()
    assert len(channels) == 2
    assert any(int(item["channel_id"]) == -1001234567890 for item in channels)

    removed = db.remove_broadcast_channel(channel_id=-1001234567890)
    assert removed is True
    channels_after = db.list_broadcast_channels()
    assert len(channels_after) == 1


def test_alert_state_and_broadcast_checkpoint(tmp_path: Path) -> None:
    db = Database(tmp_path / "alerts.db")
    db.set_last_broadcast_at("2026-01-01T00:00:00+00:00")
    assert db.get_last_broadcast_at() == "2026-01-01T00:00:00+00:00"

    db.set_last_alert_state(
        signal_hash="abc123",
        signal="BUY",
        confidence="High",
        sent_at="2026-01-01T00:10:00+00:00",
    )
    state = db.get_last_alert_state()
    assert state["hash"] == "abc123"
    assert state["signal"] == "BUY"
    assert state["confidence"] == "High"
    assert state["sent_at"] == "2026-01-01T00:10:00+00:00"


def test_signal_trigger_policy() -> None:
    assert _should_trigger_signal_alert("BUY", "High") is True
    assert _should_trigger_signal_alert("SELL", "Low") is True
    assert _should_trigger_signal_alert("SELL", "Medium") is True
    assert _should_trigger_signal_alert("SELL", "High") is False
    assert _should_trigger_signal_alert("BUY", "Medium") is False
    assert _should_trigger_signal_alert("HOLD", "High") is False


def test_extract_signal_confidence_from_curated_text() -> None:
    text = (
        "Signal: SELL\n"
        "Confidence: Medium\n"
        "Reason: US yields are rising and risk appetite improved."
    )
    signal, confidence = _extract_signal_confidence(text)
    assert signal == "SELL"
    assert confidence == "Medium"
