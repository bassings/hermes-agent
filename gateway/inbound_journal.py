"""Durable inbound journal and replay queue for gateway messages.

The journal is intentionally append-only JSONL: every inbound message gets a
``received`` row before adapter dispatch, and later lifecycle changes append
status-transition rows with the same ``journal_id``.  This keeps the hot path
simple and crash-friendly while preserving forensic ordering.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from hermes_cli.config import get_hermes_home

logger = logging.getLogger(__name__)

_DEFAULT_PREVIEW_CHARS = 256
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.now().isoformat()


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_VALUES:
            return True
        if lowered in _FALSE_VALUES:
            return False
        return default
    return bool(value)


def _config_get(*keys: str, default: Any = None) -> Any:
    try:
        from hermes_cli.config import cfg_get, load_config

        return cfg_get(load_config(), *keys, default=default)
    except Exception:
        return default


def forensic_logging_enabled() -> bool:
    """Return whether extra gateway forensic logs are enabled.

    Off by default.  Operators can enable with either env var or config:
    ``HERMES_GATEWAY_FORENSIC_LOGGING=1`` / ``HERMES_GATEWAY_FORENSICS=1`` or
    ``gateway.forensic_logging: true`` / ``gateway.forensics: true``.
    """

    for name in ("HERMES_GATEWAY_FORENSIC_LOGGING", "HERMES_GATEWAY_FORENSICS"):
        raw = os.getenv(name)
        if raw is not None:
            return _coerce_bool(raw, False)
    cfg_value = _config_get("gateway", "forensic_logging", default=None)
    if cfg_value is None:
        cfg_value = _config_get("gateway", "forensics", default=False)
    return _coerce_bool(cfg_value, False)


def _retain_full_text() -> bool:
    raw = os.getenv("HERMES_GATEWAY_INBOUND_JOURNAL_FULL_TEXT")
    if raw is not None:
        return _coerce_bool(raw, True)
    return _coerce_bool(
        _config_get("gateway", "inbound_journal_full_text", default=True),
        True,
    )


def _preview_chars() -> int:
    raw = os.getenv("HERMES_GATEWAY_INBOUND_JOURNAL_PREVIEW_CHARS")
    if raw is None:
        raw = _config_get(
            "gateway", "inbound_journal_preview_chars", default=_DEFAULT_PREVIEW_CHARS,
        )
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_PREVIEW_CHARS
    return max(16, min(value, 8192))


def _bounded_preview(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _text_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _safe_timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return _now_iso()


def _source_dict(source: Any) -> dict[str, Any]:
    if source is None:
        return {}
    if hasattr(source, "to_dict"):
        try:
            data = source.to_dict()
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    if is_dataclass(source):
        try:
            data = asdict(source)
            platform = data.get("platform")
            if platform is not None:
                data["platform"] = _enum_value(platform)
            return data
        except Exception:
            pass
    return {
        "platform": _enum_value(getattr(source, "platform", None)),
        "chat_id": getattr(source, "chat_id", None),
        "chat_name": getattr(source, "chat_name", None),
        "chat_type": getattr(source, "chat_type", None),
        "user_id": getattr(source, "user_id", None),
        "user_name": getattr(source, "user_name", None),
        "thread_id": getattr(source, "thread_id", None),
        "guild_id": getattr(source, "guild_id", None),
        "parent_chat_id": getattr(source, "parent_chat_id", None),
    }


def event_to_payload(event: Any) -> dict[str, Any]:
    """Serialize a MessageEvent into JSON-safe replay payload data."""

    source = getattr(event, "source", None)
    data = {
        "text": getattr(event, "text", "") or "",
        "message_type": _enum_value(getattr(event, "message_type", "text")),
        "source": _source_dict(source),
        "message_id": getattr(event, "message_id", None),
        "platform_update_id": getattr(event, "platform_update_id", None),
        "media_urls": list(getattr(event, "media_urls", []) or []),
        "media_types": list(getattr(event, "media_types", []) or []),
        "reply_to_message_id": getattr(event, "reply_to_message_id", None),
        "reply_to_text": getattr(event, "reply_to_text", None),
        "auto_skill": getattr(event, "auto_skill", None),
        "channel_prompt": getattr(event, "channel_prompt", None),
        "internal": bool(getattr(event, "internal", False)),
        "timestamp": _safe_timestamp(getattr(event, "timestamp", None)),
    }
    raw = getattr(event, "raw_message", None)
    if raw is not None:
        data["raw_message_repr"] = repr(raw)[:1000]
    return data


def event_from_payload(data: dict[str, Any]) -> Any:
    """Deserialize a replay payload into a MessageEvent."""

    from gateway.config import Platform
    from gateway.platforms.base import MessageEvent, MessageType
    from gateway.session import SessionSource

    source_data = dict(data.get("source") or {})
    platform_value = source_data.get("platform") or data.get("platform") or "telegram"
    source_data["platform"] = Platform(platform_value)

    allowed_source_fields = {
        "platform",
        "chat_id",
        "chat_name",
        "chat_type",
        "user_id",
        "user_name",
        "thread_id",
        "chat_topic",
        "user_id_alt",
        "chat_id_alt",
        "is_bot",
        "guild_id",
        "parent_chat_id",
        "message_id",
    }
    source = SessionSource(
        **{key: value for key, value in source_data.items() if key in allowed_source_fields}
    )

    raw_type = data.get("message_type") or "text"
    try:
        message_type = MessageType(raw_type)
    except Exception:
        message_type = MessageType.TEXT

    timestamp = datetime.now()
    raw_ts = data.get("timestamp")
    if isinstance(raw_ts, str):
        try:
            timestamp = datetime.fromisoformat(raw_ts)
        except ValueError:
            timestamp = datetime.now()

    return MessageEvent(
        text=data.get("text") or "",
        message_type=message_type,
        source=source,
        raw_message=None,
        message_id=data.get("message_id"),
        platform_update_id=data.get("platform_update_id"),
        media_urls=list(data.get("media_urls") or []),
        media_types=list(data.get("media_types") or []),
        reply_to_message_id=data.get("reply_to_message_id"),
        reply_to_text=data.get("reply_to_text"),
        auto_skill=data.get("auto_skill"),
        channel_prompt=data.get("channel_prompt"),
        internal=bool(data.get("internal", False)),
        timestamp=timestamp,
    )


def event_fingerprint(event_or_payload: Any) -> str:
    """Return the replay-dedupe fingerprint for an event or serialized event."""

    if isinstance(event_or_payload, dict):
        payload = event_or_payload
    else:
        payload = event_to_payload(event_or_payload)
    source = payload.get("source") or {}
    text = payload.get("text") or ""
    parts = [
        str(source.get("platform") or ""),
        str(source.get("chat_id") or ""),
        str(source.get("user_id") or ""),
        str(payload.get("message_id") or ""),
        str(payload.get("platform_update_id") or ""),
        _text_hash(text),
    ]
    return "|".join(parts)


class InboundJournal:
    """Append-only durable journal for inbound gateway events."""

    def __init__(self, home: Optional[Path] = None):
        self.home = Path(home) if home is not None else get_hermes_home()
        self.gateway_dir = self.home / "gateway"
        self.path = self.gateway_dir / "inbound_journal.jsonl"

    @classmethod
    def default(cls) -> "InboundJournal":
        return cls()

    def _append(self, record: dict[str, Any]) -> None:
        self.gateway_dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        with _LOCK:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def record_event(
        self,
        event: Any,
        *,
        session_key: Optional[str] = None,
        status: str = "received",
        reason: Optional[str] = None,
    ) -> str:
        journal_id = getattr(event, "_hermes_journal_id", None) or uuid.uuid4().hex
        try:
            setattr(event, "_hermes_journal_id", journal_id)
        except Exception:
            pass

        source = getattr(event, "source", None)
        source_data = _source_dict(source)
        if session_key is None:
            session_key = getattr(event, "_hermes_session_key", None)
        if session_key is None and source is not None:
            try:
                from gateway.session import build_session_key

                session_key = build_session_key(source)
            except Exception:
                session_key = None
        if session_key is not None:
            try:
                setattr(event, "_hermes_session_key", session_key)
            except Exception:
                pass

        text = getattr(event, "text", "") or ""
        preview_limit = _preview_chars()
        record: dict[str, Any] = {
            "journal_id": journal_id,
            "timestamp": _now_iso(),
            "platform": source_data.get("platform"),
            "chat_id": source_data.get("chat_id"),
            "user_id": source_data.get("user_id"),
            "user_name": source_data.get("user_name"),
            "thread_id": source_data.get("thread_id"),
            "message_id": getattr(event, "message_id", None),
            "update_id": getattr(event, "platform_update_id", None),
            "session_key": session_key,
            "message_type": _enum_value(getattr(event, "message_type", "text")),
            "text_sha256": _text_hash(text),
            "text_len": len(text),
            "text_preview": _bounded_preview(text, preview_limit),
            "status": status,
        }
        if _retain_full_text():
            record["text"] = text
        if reason:
            record["reason"] = reason
        self._append(record)
        if forensic_logging_enabled():
            logger.info(
                "Inbound journal received: journal_id=%s platform=%s chat=%s update_id=%s message_id=%s text_len=%s",
                journal_id,
                record.get("platform"),
                record.get("chat_id"),
                record.get("update_id"),
                record.get("message_id"),
                record.get("text_len"),
            )
        return journal_id

    def append_status(
        self,
        journal_id: str,
        status: str,
        *,
        event: Any = None,
        reason: Optional[str] = None,
        **extra: Any,
    ) -> None:
        if not journal_id:
            return
        source_data = _source_dict(getattr(event, "source", None)) if event is not None else {}
        record: dict[str, Any] = {
            "journal_id": journal_id,
            "timestamp": _now_iso(),
            "status": status,
        }
        if source_data:
            record.update(
                {
                    "platform": source_data.get("platform"),
                    "chat_id": source_data.get("chat_id"),
                    "user_id": source_data.get("user_id"),
                    "thread_id": source_data.get("thread_id"),
                }
            )
        if reason:
            record["reason"] = reason
        for key, value in extra.items():
            if value is not None:
                record[key] = value
        self._append(record)

    def mark_event(self, event: Any, status: str, *, reason: Optional[str] = None, **extra: Any) -> None:
        journal_id = getattr(event, "_hermes_journal_id", None)
        if not journal_id:
            journal_id = self.record_event(event)
        self.append_status(journal_id, status, event=event, reason=reason, **extra)

    def enqueue_replay(self, event: Any, *, reason: str, journal_id: Optional[str] = None) -> Path:
        if journal_id is None:
            journal_id = getattr(event, "_hermes_journal_id", None)
        if not journal_id:
            journal_id = self.record_event(event)
        queue = ReplayQueue(self.home)
        path = queue.enqueue(event, reason=reason, journal_id=journal_id)
        self.append_status(
            journal_id,
            "replay_queued",
            event=event,
            reason=reason,
            replay_path=str(path),
        )
        return path


class ReplayQueue:
    """Durable file-backed inbound replay queue."""

    def __init__(self, home: Optional[Path] = None):
        self.home = Path(home) if home is not None else get_hermes_home()
        self.queue_dir = self.home / "gateway" / "replay_queue"
        self.seen_path = self.home / "gateway" / "replay_seen.json"

    @classmethod
    def default(cls) -> "ReplayQueue":
        return cls()

    def enqueue(self, event: Any, *, reason: str, journal_id: Optional[str] = None) -> Path:
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        payload_event = event_to_payload(event)
        queue_id = uuid.uuid4().hex
        payload = {
            "queue_id": queue_id,
            "timestamp": _now_iso(),
            "reason": reason,
            "journal_id": journal_id or getattr(event, "_hermes_journal_id", None),
            "fingerprint": event_fingerprint(payload_event),
            "event": payload_event,
        }
        path = self.queue_dir / f"{datetime.now().strftime('%Y%m%d%H%M%S%f')}-{queue_id}.json"
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return path

    def iter_items(self) -> Iterable[tuple[Path, dict[str, Any]]]:
        if not self.queue_dir.exists():
            return []
        items: list[tuple[Path, dict[str, Any]]] = []
        for path in sorted(self.queue_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Replay queue item unreadable %s: %s", path, exc)
                continue
            if isinstance(payload, dict):
                items.append((path, payload))
        return items

    def event_from_item(self, payload: dict[str, Any]) -> Any:
        event = event_from_payload(payload.get("event") or {})
        try:
            setattr(event, "_hermes_replay", True)
            if payload.get("journal_id"):
                setattr(event, "_hermes_journal_id", payload.get("journal_id"))
        except Exception:
            pass
        return event

    def _load_seen(self) -> set[str]:
        try:
            data = json.loads(self.seen_path.read_text(encoding="utf-8"))
        except Exception:
            return set()
        if isinstance(data, list):
            return {str(item) for item in data}
        return set()

    def _save_seen(self, seen: set[str]) -> None:
        self.seen_path.parent.mkdir(parents=True, exist_ok=True)
        # Bound this forensic dedupe file so it cannot grow forever.
        data = sorted(seen)[-5000:]
        tmp = self.seen_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.seen_path)

    def mark_seen(self, fingerprint: str) -> None:
        if not fingerprint:
            return
        seen = self._load_seen()
        seen.add(fingerprint)
        self._save_seen(seen)

    def has_seen(self, fingerprint: str) -> bool:
        return bool(fingerprint and fingerprint in self._load_seen())

    @staticmethod
    def delete(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Failed to delete replay queue item %s: %s", path, exc)
