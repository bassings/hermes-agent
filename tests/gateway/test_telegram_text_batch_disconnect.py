import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType
from gateway.platforms.telegram import TelegramAdapter
from gateway.session import SessionSource


def _text_event(text="pending text"):
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="42",
            chat_type="dm",
            user_id="u-1",
        ),
        message_id="m-1",
        platform_update_id=999,
    )


@pytest.fixture(autouse=True)
def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


@pytest.mark.asyncio
async def test_disconnect_persists_pending_text_batch(tmp_path):
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="token"))
    event = _text_event("do not lose me")
    adapter._pending_text_batches["session-key"] = event
    adapter.handle_message = AsyncMock()

    await adapter.disconnect()

    queue_files = list((tmp_path / "gateway" / "replay_queue").glob("*.json"))
    assert len(queue_files) == 1
    payload = json.loads(queue_files[0].read_text())
    assert payload["reason"] == "telegram_disconnect_pending_text"
    assert payload["event"]["text"] == "do not lose me"
    assert adapter._pending_text_batches == {}
    adapter.handle_message.assert_not_called()


@pytest.mark.asyncio
async def test_disconnect_clears_text_batch_tasks_without_losing_event(tmp_path):
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="token"))
    event = _text_event("queued behind timer")
    task = asyncio.create_task(asyncio.sleep(60))
    adapter._pending_text_batches["session-key"] = event
    adapter._pending_text_batch_tasks["session-key"] = task

    await adapter.disconnect()
    await asyncio.sleep(0)

    assert task.cancelled()
    assert adapter._pending_text_batch_tasks == {}
    queued = list((tmp_path / "gateway" / "replay_queue").glob("*.json"))
    assert len(queued) == 1
    assert "queued behind timer" in queued[0].read_text()


@pytest.mark.asyncio
async def test_disconnect_keeps_photo_batch_existing_behaviour(tmp_path):
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="token"))
    photo_event = MessageEvent(
        text="photo caption",
        message_type=MessageType.PHOTO,
        source=SessionSource(platform=Platform.TELEGRAM, chat_id="42", chat_type="dm", user_id="u-1"),
        media_urls=["/tmp/photo.jpg"],
        media_types=["image/jpeg"],
    )
    task = asyncio.create_task(asyncio.sleep(60))
    adapter._pending_photo_batches["photo-key"] = photo_event
    adapter._pending_photo_batch_tasks["photo-key"] = task

    await adapter.disconnect()
    await asyncio.sleep(0)

    assert task.cancelled()
    assert adapter._pending_photo_batches == {}
    assert adapter._pending_photo_batch_tasks == {}
