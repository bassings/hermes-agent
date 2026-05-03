import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import gateway.run as gateway_run
from gateway.platforms.base import SendResult
from gateway.platforms.telegram import TelegramAdapter
from tests.gateway.restart_test_helpers import make_restart_runner


@pytest.mark.asyncio
async def test_invalid_telegram_restart_target_is_skipped_cleanly(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    notify_path = tmp_path / ".restart_notify.json"
    notify_path.write_text(json.dumps({"platform": "telegram", "chat_id": "e2e-chat-1"}))
    runner, adapter = make_restart_runner()
    adapter.send = AsyncMock(return_value=SendResult(success=True, message_id="should-not-send"))
    caplog.set_level(logging.INFO)

    await runner._send_restart_notification()

    adapter.send.assert_not_called()
    assert "invalid restart notification target" in caplog.text.lower()
    assert "Sent restart notification" not in caplog.text
    assert not notify_path.exists()


@pytest.mark.asyncio
async def test_valid_telegram_restart_target_still_sends(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    notify_path = tmp_path / ".restart_notify.json"
    notify_path.write_text(json.dumps({"platform": "telegram", "chat_id": "-10042"}))
    runner, adapter = make_restart_runner()
    adapter.send = AsyncMock(return_value=SendResult(success=True, message_id="out-1"))
    caplog.set_level(logging.INFO)

    await runner._send_restart_notification()

    adapter.send.assert_awaited_once()
    assert adapter.send.await_args.args[0] == "-10042"
    assert "Sent restart notification" in caplog.text
    assert not notify_path.exists()


@pytest.mark.asyncio
async def test_no_false_sent_log_for_invalid_restart_target(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    notify_path = tmp_path / ".restart_notify.json"
    notify_path.write_text(json.dumps({"platform": "telegram", "chat_id": "e2e-chat-1"}))
    runner, adapter = make_restart_runner()
    adapter.send = AsyncMock(return_value=SendResult(success=False, error="invalid-chat-id"))
    caplog.set_level(logging.INFO)

    await runner._send_restart_notification()

    assert "Sent restart notification" not in caplog.text
    assert "invalid restart notification target" in caplog.text.lower()


@pytest.mark.asyncio
async def test_telegram_send_rejects_non_numeric_chat_id_without_traceback(caplog):
    from gateway.config import PlatformConfig

    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="token"))
    adapter._bot = SimpleNamespace(send_message=AsyncMock())
    caplog.set_level(logging.WARNING)

    result = await adapter.send("e2e-chat-1", "hello")

    assert result.success is False
    assert result.error == "invalid-chat-id"
    adapter._bot.send_message.assert_not_called()
    assert "invalid Telegram chat_id" in caplog.text
