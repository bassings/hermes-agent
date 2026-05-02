"""Tests for the GCB full manifest/state-machine helper."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("HERMES_LOCAL_SKILL_TESTS") != "1",
    reason="local GCB skill integration tests require HERMES_LOCAL_SKILL_TESTS=1",
)

SCRIPT_DIR = Path.home() / ".hermes" / "skills" / "gcb" / "gcb-operations" / "scripts"


def _load_script(name: str):
    path = SCRIPT_DIR / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader, f"cannot import {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


def test_create_job_locks_source_and_zero_images_selects_library_gallery(tmp_path):
    orch = _load_script("gcb_full_orchestrator.py")

    state = orch.create_job(
        source_text="Supplied source text",
        latest_user_message="gcb full 0 images",
        expected_image_count=0,
        story_slug="test-story",
        root_dir=tmp_path,
        owner_agent_id="parent-1",
    )

    job_dir = tmp_path / state["job_id"]
    assert state["phase"] == "SOURCE_LOCKED"
    assert state["expected_image_count"] == 0
    assert state["media_mode"] == "library_gallery"
    assert state["canonical_source_hash"]
    assert state["latest_user_message_hash"]
    assert state["selected_shell_post_id"] is None
    assert state["draft_id"] is None
    assert state["verification_result"] == {}
    assert (job_dir / "state.json").exists()
    assert (job_dir / "source.txt").read_text(encoding="utf-8") == "Supplied source text"


def test_draft_package_requires_gates_and_matching_image_count(tmp_path):
    orch = _load_script("gcb_full_orchestrator.py")
    state = orch.create_job("source", "gcb full last 2 images", 2, root_dir=tmp_path)

    blocked = orch.transition(
        state,
        "DRAFT_PACKAGE_READY",
        actual_image_count=1,
        gate_results={"style": {"ok": True}, "anti_ai": {"ok": True}, "copy": {"ok": True}},
    )

    assert blocked["phase"] == "FAILED"
    assert blocked["terminal_state"] == "FAILED"
    assert "image-count-mismatch" in blocked["errors"]

    state = orch.create_job("source", "gcb full last 2 images", 2, root_dir=tmp_path)
    ready = orch.transition(
        state,
        "DRAFT_PACKAGE_READY",
        actual_image_count=2,
        gate_results={"style": {"ok": True}, "anti_ai": {"ok": True}, "copy": {"ok": True}},
    )

    assert ready["phase"] == "DRAFT_PACKAGE_READY"
    assert ready["terminal_state"] is None


def test_completion_only_from_verified_and_delivery_is_terminal(tmp_path):
    orch = _load_script("gcb_full_orchestrator.py")
    state = orch.create_job("source", "gcb full", 1, root_dir=tmp_path)

    assert orch.completion_payload(state)["ok"] is False

    state = orch.transition(state, "DRAFT_PACKAGE_READY", actual_image_count=1, gate_results={
        "style": {"ok": True}, "anti_ai": {"ok": True}, "copy": {"ok": True}
    })
    state = orch.transition(
        state,
        "DRAFT_WRITTEN",
        post_id=123,
        draft_edit_url="https://example/edit/123",
        selected_shell_post_id=99,
    )
    state = orch.transition(state, "VERIFIED", verification={"ok": True, "media_ids": [1, 2], "style_lint": {"ok": True}})
    payload = orch.completion_payload(state)

    assert state["draft_id"] == 123
    assert state["selected_shell_post_id"] == 99
    assert state["verification_result"]["ok"] is True
    assert payload["ok"] is True
    assert payload["post_id"] == 123
    assert payload["draft_edit_url"] == "https://example/edit/123"

    delivered = orch.transition(state, "DELIVERED")
    assert delivered["phase"] == "DELIVERED"
    assert delivered["terminal_state"] == "DELIVERED"

    unchanged = orch.transition(delivered, "VERIFIED", verification={"ok": True, "style_lint": {"ok": True}})
    assert unchanged == delivered


def test_final_verification_requires_copy_style_lint(tmp_path):
    orch = _load_script("gcb_full_orchestrator.py")
    state = orch.create_job("source", "gcb full", 1, root_dir=tmp_path)
    state = orch.transition(state, "DRAFT_PACKAGE_READY", actual_image_count=1, gate_results={
        "style": {"ok": True}, "anti_ai": {"ok": True}, "copy": {"ok": True}
    })
    state = orch.transition(state, "DRAFT_WRITTEN", post_id=123, draft_edit_url="https://example/edit/123")

    failed = orch.transition(state, "VERIFIED", verification={"ok": True})

    assert failed["phase"] == "FAILED"
    assert "copy-style-lint-not-passed" in failed["errors"]


def test_final_verification_runs_once(tmp_path):
    orch = _load_script("gcb_full_orchestrator.py")
    state = orch.create_job("source", "gcb full", 1, root_dir=tmp_path)
    state = orch.transition(state, "DRAFT_PACKAGE_READY", actual_image_count=1, gate_results={
        "style": {"ok": True}, "anti_ai": {"ok": True}, "copy": {"ok": True}
    })
    state = orch.transition(state, "DRAFT_WRITTEN", post_id=123, draft_edit_url="https://example/edit/123")
    verified = orch.transition(state, "VERIFIED", verification={"ok": True, "style_lint": {"ok": True}})
    failed_second = orch.transition(verified, "VERIFIED", verification={"ok": True, "style_lint": {"ok": True}})

    assert verified["phase"] == "VERIFIED"
    assert failed_second["phase"] == "FAILED"
    assert "second-verification-not-allowed" in failed_second["errors"]


def test_timebox_deadline_and_expired_failure_lines(tmp_path):
    orch = _load_script("gcb_full_orchestrator.py")

    state = orch.create_job(
        "source",
        "you have 3 minutes, gcb full",
        1,
        root_dir=tmp_path,
        now_monotonic=100.0,
    )

    assert state["deadline_monotonic"] == 280.0
    assert orch.deadline_expired(state, now_monotonic=279.0) is False
    assert orch.deadline_expired(state, now_monotonic=281.0) is True
    assert orch.timebox_failure_line(state, now_monotonic=281.0) == "No draft exists."

    written = orch.transition(state, "DRAFT_WRITTEN", post_id=5, draft_edit_url="https://example/edit/5")
    assert orch.timebox_failure_line(written, now_monotonic=281.0) == "No verified draft exists."


def test_worker_results_are_artifacts_not_self_report(tmp_path):
    orch = _load_script("gcb_full_orchestrator.py")
    state = orch.create_job("source", "gcb full", 1, root_dir=tmp_path)
    result = orch.record_worker_result(
        state,
        {"worker_id": "copy-1", "role": "copy", "status": "completed", "artifact_path": str(tmp_path / state["job_id"] / "copy.json")},
    )

    assert result["workers"]["copy-1"]["artifact_exists"] is False
    assert result["workers"]["copy-1"]["status"] == "ignored"

    artifact = tmp_path / state["job_id"] / "copy.json"
    artifact.write_text(json.dumps({"headline": "Real copy"}), encoding="utf-8")
    result = orch.record_worker_result(state, {"worker_id": "copy-1", "role": "copy", "status": "completed", "artifact_path": str(artifact)})

    assert result["workers"]["copy-1"]["artifact_exists"] is True
    assert result["workers"]["copy-1"]["status"] == "completed"
