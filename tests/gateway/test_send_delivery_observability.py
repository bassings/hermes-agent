import asyncio
import logging

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, MessageEvent, MessageType, SendResult
from gateway.session import SessionSource


class DeliveryTestAdapter(BasePlatformAdapter):
    def __init__(self, result):
        super().__init__(PlatformConfig(enabled=True, token="token"), Platform.TELEGRAM)
        self._result = result
        self.sent = []

    async def connect(self):
        return True

    async def disconnect(self):
        return None

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        self.sent.append((chat_id, content, reply_to, metadata))
        if isinstance(self._result, Exception):
            raise self._result
        return self._result

    async def send_typing(self, chat_id, metadata=None):
        return None

    async def stop_typing(self, chat_id):
        return None

    async def get_chat_info(self, chat_id):
        return {"id": chat_id}


def _event():
    return MessageEvent(
        text="hello",
        message_type=MessageType.TEXT,
        source=SessionSource(platform=Platform.TELEGRAM, chat_id="42", chat_type="dm", user_id="u-1"),
        message_id="incoming-1",
    )


@pytest.fixture(autouse=True)
def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


@pytest.mark.asyncio
async def test_send_logs_attempt_and_success_with_message_id(caplog, monkeypatch):
    monkeypatch.setenv("HERMES_GATEWAY_FORENSIC_LOGGING", "1")
    adapter = DeliveryTestAdapter(SendResult(success=True, message_id="out-1", raw_response={"message_ids": ["out-1"]}))
    adapter.set_message_handler(lambda _event: asyncio.sleep(0, result="response text"))
    caplog.set_level(logging.INFO)

    await adapter.handle_message(_event())
    if adapter._background_tasks:
        await asyncio.gather(*list(adapter._background_tasks), return_exceptions=True)

    assert "Sending response" in caplog.text
    assert "Response send succeeded" in caplog.text
    assert "out-1" in caplog.text


@pytest.mark.asyncio
async def test_send_logs_failure_without_false_success(caplog):
    adapter = DeliveryTestAdapter(SendResult(success=False, error="permission denied"))
    adapter.set_message_handler(lambda _event: asyncio.sleep(0, result="response text"))
    caplog.set_level(logging.INFO)

    await adapter.handle_message(_event())
    if adapter._background_tasks:
        await asyncio.gather(*list(adapter._background_tasks), return_exceptions=True)

    assert "Response send failed" in caplog.text
    assert "permission denied" in caplog.text
    assert "Response send succeeded" not in caplog.text
