import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import gateway.run as gateway_run
from gateway.config import Platform
from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionSource
from tests.gateway.restart_test_helpers import make_restart_runner


def _telegram_event(text="please keep this after restart", *, message_id="m-1", update_id=1001):
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="42",
            chat_type="dm",
            user_id="u-1",
            user_name="Alan",
        ),
        message_id=message_id,
        platform_update_id=update_id,
    )


@pytest.fixture(autouse=True)
def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    return tmp_path


@pytest.mark.asyncio
async def test_stale_code_queues_triggering_message_for_replay(tmp_path, monkeypatch):
    runner, _adapter = make_restart_runner()
    runner._detect_stale_code = MagicMock(return_value=True)
    runner._trigger_stale_code_restart = MagicMock()

    response = await runner._handle_message(_telegram_event(text="lost no more"))

    assert "restarting" in response.lower()
    queue_dir = tmp_path / "gateway" / "replay_queue"
    queued_files = list(queue_dir.glob("*.json"))
    assert len(queued_files) == 1
    queued = json.loads(queued_files[0].read_text())
    assert queued["reason"] == "stale_code_restart"
    assert queued["event"]["text"] == "lost no more"
    assert queued["event"]["platform_update_id"] == 1001

    journal_rows = [
        json.loads(line)
        for line in (tmp_path / "gateway" / "inbound_journal.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert any(row["status"] == "replay_queued" and row.get("reason") == "stale_code_restart" for row in journal_rows)
    runner._trigger_stale_code_restart.assert_called_once()


@pytest.mark.asyncio
async def test_stale_code_replay_runs_after_restart_startup(tmp_path):
    from gateway.inbound_journal import ReplayQueue

    queue = ReplayQueue(home=tmp_path)
    queue.enqueue(_telegram_event(text="run me after restart"), reason="stale_code_restart")

    runner, adapter = make_restart_runner()
    adapter.handle_message = AsyncMock()

    await runner._drain_inbound_replay_queue()

    adapter.handle_message.assert_awaited_once()
    replayed_event = adapter.handle_message.await_args.args[0]
    assert replayed_event.text == "run me after restart"
    assert getattr(replayed_event, "_hermes_replay", False) is True
    assert list((tmp_path / "gateway" / "replay_queue").glob("*.json")) == []


@pytest.mark.asyncio
async def test_replay_dedupe_prevents_duplicate_processing(tmp_path):
    from gateway.inbound_journal import ReplayQueue

    event = _telegram_event(text="dedupe me", message_id="same", update_id=55)
    queue = ReplayQueue(home=tmp_path)
    queue.enqueue(event, reason="stale_code_restart")
    queue.enqueue(event, reason="stale_code_restart")

    runner, adapter = make_restart_runner()
    adapter.handle_message = AsyncMock()

    await runner._drain_inbound_replay_queue()

    adapter.handle_message.assert_awaited_once()
    assert list((tmp_path / "gateway" / "replay_queue").glob("*.json")) == []


@pytest.mark.asyncio
async def test_stale_code_no_longer_creates_flush_without_recoverable_inbound(tmp_path):
    runner, _adapter = make_restart_runner()
    runner._detect_stale_code = MagicMock(return_value=True)
    runner._trigger_stale_code_restart = MagicMock()

    await runner._handle_message(_telegram_event(text="recoverable 12:17 text"))

    journal_path = tmp_path / "gateway" / "inbound_journal.jsonl"
    queue_files = list((tmp_path / "gateway" / "replay_queue").glob("*.json"))
    assert journal_path.exists()
    assert queue_files
    assert "recoverable 12:17 text" in queue_files[0].read_text()
