import asyncio
import hashlib
import json
import logging

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, MessageEvent, MessageType, SendResult
from gateway.session import SessionSource


class JournalTestAdapter(BasePlatformAdapter):
    def __init__(self):
        super().__init__(PlatformConfig(enabled=True, token="test-token"), Platform.TELEGRAM)
        self.sent = []

    async def connect(self):
        return True

    async def disconnect(self):
        return None

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        self.sent.append((chat_id, content, reply_to, metadata))
        return SendResult(success=True, message_id="sent-1", raw_response={"message_ids": ["sent-1"]})

    async def send_typing(self, chat_id, metadata=None):
        return None

    async def stop_typing(self, chat_id):
        return None

    async def get_chat_info(self, chat_id):
        return {"id": chat_id}


@pytest.fixture(autouse=True)
def _journal_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_GATEWAY_INBOUND_JOURNAL_FULL_TEXT", raising=False)
    monkeypatch.delenv("HERMES_GATEWAY_INBOUND_JOURNAL_PREVIEW_CHARS", raising=False)
    monkeypatch.delenv("HERMES_GATEWAY_FORENSIC_LOGGING", raising=False)
    return tmp_path


def _event(text="hello from telegram", *, message_id="m-1", update_id=9876):
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="42",
            chat_type="dm",
            user_id="u-1",
            user_name="Alan",
            thread_id="topic-1",
        ),
        message_id=message_id,
        platform_update_id=update_id,
    )


def _journal_rows(home):
    path = home / "gateway" / "inbound_journal.jsonl"
    assert path.exists()
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@pytest.mark.asyncio
async def test_handle_message_writes_inbound_journal_before_processing(tmp_path):
    adapter = JournalTestAdapter()
    handler_started = asyncio.Event()

    async def handler(event):
        rows = _journal_rows(tmp_path)
        assert rows[0]["status"] == "received"
        assert rows[0]["text"] == event.text
        handler_started.set()
        return "ok"

    adapter.set_message_handler(handler)

    await adapter.handle_message(_event())
    await asyncio.wait_for(handler_started.wait(), timeout=2)
    if adapter._background_tasks:
        await asyncio.gather(*list(adapter._background_tasks), return_exceptions=True)


@pytest.mark.asyncio
async def test_journal_record_contains_update_id_message_id_and_text_hash(tmp_path):
    adapter = JournalTestAdapter()
    adapter.set_message_handler(lambda _event: asyncio.sleep(0, result="ok"))
    text = "hash me before dispatch"

    await adapter.handle_message(_event(text=text, message_id="msg-99", update_id=123456))
    if adapter._background_tasks:
        await asyncio.gather(*list(adapter._background_tasks), return_exceptions=True)

    received = _journal_rows(tmp_path)[0]
    assert received["platform"] == "telegram"
    assert received["chat_id"] == "42"
    assert received["user_id"] == "u-1"
    assert received["thread_id"] == "topic-1"
    assert received["message_id"] == "msg-99"
    assert received["update_id"] == 123456
    assert received["text_len"] == len(text)
    assert received["text_sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert received["message_type"] == "text"
    assert received["session_key"].startswith("agent:main:telegram")


@pytest.mark.asyncio
async def test_journal_survives_runner_short_circuit(tmp_path):
    adapter = JournalTestAdapter()

    async def short_circuit(_event):
        return "short-circuit response"

    adapter.set_message_handler(short_circuit)

    await adapter.handle_message(_event("trigger short circuit"))
    if adapter._background_tasks:
        await asyncio.gather(*list(adapter._background_tasks), return_exceptions=True)

    statuses = [row["status"] for row in _journal_rows(tmp_path)]
    assert "received" in statuses
    assert "dispatch_started" in statuses
    assert "response_attempted" in statuses
    assert "response_sent" in statuses


def test_journal_bounds_long_text_preview_when_full_text_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_GATEWAY_INBOUND_JOURNAL_FULL_TEXT", "0")
    monkeypatch.setenv("HERMES_GATEWAY_INBOUND_JOURNAL_PREVIEW_CHARS", "32")

    from gateway.inbound_journal import InboundJournal

    text = "x" * 200
    journal = InboundJournal(home=tmp_path)
    journal.record_event(_event(text=text))

    row = _journal_rows(tmp_path)[0]
    assert "text" not in row
    assert row["text_len"] == 200
    assert len(row["text_preview"]) <= 33
    assert row["text_preview"].endswith("…")
    assert row["text_sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_forensic_logging_flag_is_off_by_default_and_env_enabled(monkeypatch, tmp_path, caplog):
    from gateway.inbound_journal import InboundJournal

    caplog.set_level(logging.INFO)
    InboundJournal(home=tmp_path).record_event(_event("default quiet"))
    assert "Inbound journal received" not in caplog.text

    caplog.clear()
    monkeypatch.setenv("HERMES_GATEWAY_FORENSIC_LOGGING", "1")
    InboundJournal(home=tmp_path).record_event(_event("flagged forensic"))
    assert "Inbound journal received" in caplog.text
