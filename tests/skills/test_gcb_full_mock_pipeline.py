"""Mock end-to-end tests for the local GCB full pipeline helper.

These tests use deterministic fake adapters only. They must not touch live
WordPress, Telegram, media uploads, or network services.
"""

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


def test_mock_pipeline_happy_path_secures_media_before_copy_and_returns_verified_payload(tmp_path):
    orch = _load_script("gcb_full_orchestrator.py")
    media_ids = [112876, 112879, 112878, 112877, 112881, 112880, 112883, 112882, 112884, 112885, 112886]

    result = orch.run_mock_pipeline(
        source_text="Auto Union source text",
        latest_user_message="gcb full 11 images",
        expected_image_count=11,
        story_slug="auto-union-lucca-mock",
        root_dir=tmp_path,
        media_ids=media_ids,
        media_seconds=24.0,
        body_text="Auto Union has A & B angles in this mock story.",
    )

    state = result["state"]
    assert result["ok"] is True
    assert result["completion_payload"]["ok"] is True
    assert state["phase"] == "VERIFIED"
    assert state["media_ready_at_monotonic"] == 24.0
    assert state["copy_started_at_monotonic"] >= state["media_ready_at_monotonic"]
    assert state["media_ids"] == media_ids
    assert result["operations"].index("media.ready") < result["operations"].index("copy.ready")
    assert result["operations"].index("copy.ready") < result["operations"].index("wp.create_draft")
    assert result["draft"]["edit_url"].startswith("https://mock.gcb/wp-admin/post.php?post=")

    media_manifest = json.loads((Path(state["job_dir"]) / "media.json").read_text(encoding="utf-8"))
    assert media_manifest["media_ids"] == media_ids
    assert media_manifest["featured_media_id"] == media_ids[0]


def test_mock_pipeline_media_gate_expiry_prevents_lint_and_draft_creation(tmp_path):
    orch = _load_script("gcb_full_orchestrator.py")

    result = orch.run_mock_pipeline(
        source_text="source",
        latest_user_message="gcb full 3 images",
        expected_image_count=3,
        root_dir=tmp_path,
        media_ids=[1, 2, 3],
        media_seconds=61.0,
    )

    state = result["state"]
    assert result["ok"] is False
    assert state["phase"] == "FAILED"
    assert "media-gate-expired" in state["errors"]
    assert result["alan_report_line"] == "Images not ready."
    assert "lint.run" not in result["operations"]
    assert "wp.create_draft" not in result["operations"]
    assert result["draft"] == {}
    assert result["lint_runs"] == 0


def test_mock_pipeline_story_ampersand_stays_in_payload_not_shell_command(tmp_path):
    orch = _load_script("gcb_full_orchestrator.py")

    result = orch.run_mock_pipeline(
        source_text="source",
        latest_user_message="gcb full 1 image",
        expected_image_count=1,
        root_dir=tmp_path,
        media_ids=[112876],
        body_text="A & B must be JSON payload data, not shell syntax.",
    )

    script = result["script"]
    assert result["ok"] is True
    assert "&" not in script["shell_command"]
    payload_text = Path(script["payload_path"]).read_text(encoding="utf-8")
    assert "A & B must be JSON payload data" in payload_text


def test_mock_pipeline_timed_style_lint_failure_does_not_retry_or_block(tmp_path):
    orch = _load_script("gcb_full_orchestrator.py")

    result = orch.run_mock_pipeline(
        source_text="source",
        latest_user_message="you have 1 minutes, gcb full",
        expected_image_count=1,
        root_dir=tmp_path,
        media_ids=[112876],
        style_ok=False,
    )

    state = result["state"]
    assert result["ok"] is True
    assert state["lint_mode"] == "timed_one_shot"
    assert result["lint_runs"] == 1
    assert state["gate_results"]["style"]["ok"] is False
    assert state["verification_result"]["style_lint"]["ok"] is False
    assert state["phase"] == "VERIFIED"


def test_mock_pipeline_copy_as_is_bypasses_lint_without_repair_loop(tmp_path):
    orch = _load_script("gcb_full_orchestrator.py")

    result = orch.run_mock_pipeline(
        source_text="source",
        latest_user_message="build with copy as is. gcb full 1 image. go",
        expected_image_count=1,
        root_dir=tmp_path,
        media_ids=[112876],
        style_ok=False,
    )

    state = result["state"]
    assert result["ok"] is True
    assert state["lint_mode"] == "bypass_user_ordered"
    assert state["gate_results"] == {}
    assert result["lint_runs"] == 0
    assert state["verification_result"]["lint_override"]["reason"] == "user-ordered-copy-as-is"


def test_mock_pipeline_zero_images_selects_library_gallery_media(tmp_path):
    orch = _load_script("gcb_full_orchestrator.py")
    library_ids = [1001, 1002, 1003]

    result = orch.run_mock_pipeline(
        source_text="source",
        latest_user_message="gcb full 0 images",
        expected_image_count=0,
        root_dir=tmp_path,
        media_ids=library_ids,
        media_source="library",
    )

    state = result["state"]
    assert result["ok"] is True
    assert state["media_mode"] == "library_gallery"
    assert state["actual_image_count"] == len(library_ids)
    assert state["media_ids"] == library_ids
    assert "media.library" in result["operations"]
    assert result["completion_payload"]["media_ids"] == library_ids


def test_mock_pipeline_protocol_clean_failure_blocks_completion(tmp_path):
    orch = _load_script("gcb_full_orchestrator.py")

    result = orch.run_mock_pipeline(
        source_text="source",
        latest_user_message="gcb full 1 image",
        expected_image_count=1,
        root_dir=tmp_path,
        media_ids=[112876],
        shell_copy_ok=False,
    )

    state = result["state"]
    assert result["ok"] is False
    assert state["phase"] == "FAILED"
    assert "shell-copy-verification-not-passed" in state["errors"]
    assert "wp.create_draft" in result["operations"]
    assert result["completion_payload"] == {"ok": False, "error": "completion-requires-verified"}


def test_mock_pipeline_stop_after_media_keeps_media_manifest_and_prevents_draft(tmp_path):
    orch = _load_script("gcb_full_orchestrator.py")

    result = orch.run_mock_pipeline(
        source_text="source",
        latest_user_message="gcb full 2 images",
        expected_image_count=2,
        root_dir=tmp_path,
        media_ids=[112876, 112879],
        stop_after_media=True,
    )

    state = result["state"]
    assert result["ok"] is False
    assert state["phase"] == "STOPPED"
    assert state["terminal_state"] == "STOPPED"
    assert "wp.create_draft" not in result["operations"]
    assert result["draft"] == {}
    media_manifest = json.loads((Path(state["job_dir"]) / "media.json").read_text(encoding="utf-8"))
    assert media_manifest["media_ids"] == [112876, 112879]
