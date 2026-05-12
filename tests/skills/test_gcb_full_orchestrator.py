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


def _manifest_path(tmp_path: Path, *, reference_post_id: int = 99, reference_hash: str = "refhash") -> str:
    path = tmp_path / f"manifest-{reference_post_id}.json"
    path.write_text(
        json.dumps(
            {
                "method": "build-from-shell",
                "builder": "gcb_wp_draft_guard.py",
                "builder_version": 2,
                "reference_post_id": reference_post_id,
                "reference_content_sha256": reference_hash,
                "fixed_shell_spans": {},
            }
        ),
        encoding="utf-8",
    )
    return str(path)


def _copied_visible(orch, state: dict, tmp_path: Path, *, reference_post_id: int = 99, reference_hash: str = "refhash") -> dict:
    return orch.transition(
        state,
        "PREVIOUS_POST_COPIED_VISIBLE",
        draft_id=123,
        draft_edit_url="https://example/edit/123",
        selected_shell_post_id=reference_post_id,
        reference_post_id=reference_post_id,
        reference_content_sha256=reference_hash,
        now_monotonic=45.0,
    )


def _package_ready(orch, tmp_path: Path) -> dict:
    state = orch.create_job("source", "gcb full", 1, root_dir=tmp_path)
    state = orch.transition(
        state,
        "MEDIA_READY",
        actual_image_count=1,
        media_ids=[1],
        featured_media_id=1,
    )
    state = _copied_visible(orch, state, tmp_path)
    state = orch.transition(state, "COPY_READY")
    return orch.transition(
        state,
        "DRAFT_PACKAGE_READY",
        actual_image_count=1,
        gate_results={"style": {"ok": True}, "anti_ai": {"ok": True}, "copy": {"ok": True}},
    )


def _draft_written(orch, tmp_path: Path, *, reference_post_id: int = 99, reference_hash: str = "refhash") -> dict:
    state = _package_ready(orch, tmp_path)
    return orch.transition(
        state,
        "DRAFT_WRITTEN",
        post_id=123,
        draft_edit_url="https://example/edit/123",
        selected_shell_post_id=reference_post_id,
        reference_post_id=reference_post_id,
        reference_content_sha256=reference_hash,
        draft_build_method="build-from-shell",
        draft_build_manifest_path=_manifest_path(tmp_path, reference_post_id=reference_post_id, reference_hash=reference_hash),
    )


def _verification() -> dict:
    return {"ok": True, "media_ids": [1, 2], "style_lint": {"ok": True}}


def _shell_copy_verification() -> dict:
    return {"ok": True, "protocol_clean": True, "issues": []}


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
    assert state["media_first_required"] is True
    assert state["media_gate_seconds"] == 60
    assert state["media_gate_deadline_monotonic"] is not None
    assert state["media_ready_at_monotonic"] is None
    assert state["lint_mode"] == "strict"
    assert state["canonical_source_hash"]
    assert state["latest_user_message_hash"]
    assert state["selected_shell_post_id"] is None
    assert state["shell_required"] is True
    assert state["reference_post_id"] is None
    assert state["reference_content_sha256"] == ""
    assert state["draft_build_method"] == ""
    assert state["draft_build_manifest_path"] == ""
    assert state["shell_copy_verification"] == {}
    assert state["protocol_clean"] is False
    assert state["draft_id"] is None
    assert state["verification_result"] == {}
    assert (job_dir / "state.json").exists()
    assert (job_dir / "source.txt").read_text(encoding="utf-8") == "Supplied source text"



def test_media_gate_blocks_copy_until_media_ready_and_reports_expiry(tmp_path):
    orch = _load_script("gcb_full_orchestrator.py")
    state = orch.create_job("source", "gcb full 1 image", 1, root_dir=tmp_path, now_monotonic=10.0)

    blocked = orch.transition(state, "COPY_READY", now_monotonic=30.0)
    assert blocked["phase"] == "FAILED"
    assert "media-required-before-copy" in blocked["errors"]
    assert orch.media_gate_expired(state, now_monotonic=69.0) is False
    assert orch.media_gate_expired(state, now_monotonic=71.0) is True
    assert orch.media_gate_failure_line(state, now_monotonic=71.0) == "Images not ready."

    expired = orch.transition(state, "COPY_READY", now_monotonic=71.0)
    assert expired["phase"] == "FAILED"
    assert "media-gate-expired" in expired["errors"]

    fresh = orch.create_job("source", "gcb full 1 image", 1, root_dir=tmp_path, now_monotonic=10.0)
    media_ready = orch.transition(
        fresh,
        "MEDIA_READY",
        actual_image_count=1,
        media_ids=[112876],
        featured_media_id=112876,
        now_monotonic=40.0,
    )
    assert media_ready["phase"] == "MEDIA_READY"
    assert media_ready["media_ready_at_monotonic"] == 40.0
    assert (tmp_path / media_ready["job_id"] / "media.json").exists()

    copied = _copied_visible(orch, media_ready, tmp_path)
    assert copied["phase"] == "PREVIOUS_POST_COPIED_VISIBLE"
    assert copied["previous_post_copy_gate"] is True

    copy_ready = orch.transition(copied, "COPY_READY", now_monotonic=45.0)
    assert copy_ready["phase"] == "COPY_READY"
    assert copy_ready["copy_started_at_monotonic"] == 45.0



def test_library_gallery_media_ready_uses_selected_ids_not_zero_match(tmp_path):
    orch = _load_script("gcb_full_orchestrator.py")
    state = orch.create_job("source", "gcb full 0 images", 0, root_dir=tmp_path)

    media_ready = orch.transition(
        state,
        "MEDIA_READY",
        actual_image_count=3,
        media_ids=[1001, 1002, 1003],
        featured_media_id=1001,
    )

    assert media_ready["phase"] == "MEDIA_READY"
    assert media_ready["media_mode"] == "library_gallery"
    assert media_ready["actual_image_count"] == 3
    assert media_ready["media_ids"] == [1001, 1002, 1003]

    package_ready = orch.transition(
        media_ready,
        "DRAFT_PACKAGE_READY",
        actual_image_count=3,
        gate_results={"style": {"ok": True}, "anti_ai": {"ok": True}, "copy": {"ok": True}},
    )
    assert package_ready["phase"] == "DRAFT_PACKAGE_READY"



def test_draft_package_requires_gates_and_matching_image_count(tmp_path):
    orch = _load_script("gcb_full_orchestrator.py")
    state = orch.create_job("source", "gcb full last 2 images", 2, root_dir=tmp_path)

    blocked = orch.transition(
        state,
        "MEDIA_READY",
        actual_image_count=1,
        media_ids=[1],
        featured_media_id=1,
    )

    assert blocked["phase"] == "FAILED"
    assert blocked["terminal_state"] == "FAILED"
    assert "image-count-mismatch" in blocked["errors"]

    state = orch.create_job("source", "gcb full last 2 images", 2, root_dir=tmp_path)
    state = orch.transition(
        state,
        "MEDIA_READY",
        actual_image_count=2,
        media_ids=[1, 2],
        featured_media_id=1,
    )
    ready = orch.transition(
        state,
        "DRAFT_PACKAGE_READY",
        actual_image_count=2,
        gate_results={"style": {"ok": True}, "anti_ai": {"ok": True}, "copy": {"ok": True}},
    )

    assert ready["phase"] == "DRAFT_PACKAGE_READY"
    assert ready["terminal_state"] is None



def test_timed_lint_mode_records_style_failure_but_does_not_block(tmp_path):
    orch = _load_script("gcb_full_orchestrator.py")
    state = orch.create_job("source", "you have 1 minutes, gcb full", 1, root_dir=tmp_path, now_monotonic=10.0)
    assert state["lint_mode"] == "timed_one_shot"
    state = orch.transition(state, "MEDIA_READY", actual_image_count=1, media_ids=[1], featured_media_id=1)

    ready = orch.transition(
        state,
        "DRAFT_PACKAGE_READY",
        actual_image_count=1,
        gate_results={
            "style": {"ok": False, "issues": [{"rule_id": "banned-the-sort-of"}]},
            "anti_ai": {"ok": True},
            "copy": {"ok": True},
        },
    )

    assert ready["phase"] == "DRAFT_PACKAGE_READY"
    assert ready["gate_results"]["style"]["ok"] is False

    written = orch.transition(
        ready,
        "DRAFT_WRITTEN",
        post_id=123,
        draft_edit_url="https://example/edit/123",
        selected_shell_post_id=99,
        reference_post_id=99,
        reference_content_sha256="refhash",
        draft_build_method="build-from-shell",
        draft_build_manifest_path=_manifest_path(tmp_path),
    )
    verified = orch.transition(
        written,
        "VERIFIED",
        verification={
            "ok": True,
            "media_ids": [1],
            "style_lint": {"ok": False, "issues": [{"rule_id": "banned-the-sort-of"}]},
        },
        shell_copy_verification=_shell_copy_verification(),
        protocol_clean=True,
    )

    assert verified["phase"] == "VERIFIED"
    assert verified["verification_result"]["style_lint"]["ok"] is False



def test_user_ordered_copy_as_is_bypasses_lint_as_blocker(tmp_path):
    orch = _load_script("gcb_full_orchestrator.py")
    state = orch.create_job("source", "build with copy as is. 1 minute. go", 1, root_dir=tmp_path)
    assert state["lint_mode"] == "bypass_user_ordered"
    state = orch.transition(state, "MEDIA_READY", actual_image_count=1, media_ids=[1], featured_media_id=1)

    ready = orch.transition(state, "DRAFT_PACKAGE_READY", actual_image_count=1, gate_results={})
    assert ready["phase"] == "DRAFT_PACKAGE_READY"

    written = orch.transition(
        ready,
        "DRAFT_WRITTEN",
        post_id=123,
        draft_edit_url="https://example/edit/123",
        selected_shell_post_id=99,
        reference_post_id=99,
        reference_content_sha256="refhash",
        draft_build_method="build-from-shell",
        draft_build_manifest_path=_manifest_path(tmp_path),
    )
    verified = orch.transition(
        written,
        "VERIFIED",
        verification={"ok": True, "media_ids": [1], "lint_override": {"reason": "user-ordered-copy-as-is"}},
        shell_copy_verification=_shell_copy_verification(),
        protocol_clean=True,
    )

    assert verified["phase"] == "VERIFIED"
    assert verified["verification_result"]["lint_override"]["reason"] == "user-ordered-copy-as-is"



def test_recoverable_script_artifacts_keep_payload_text_out_of_shell_command(tmp_path):
    orch = _load_script("gcb_full_orchestrator.py")
    job_dir = tmp_path / "job"
    payload = {"body": "A & B should never appear in the shell command"}

    result = orch.write_recoverable_script(
        job_dir,
        script_name="build_draft.py",
        script_source="import json, sys\nprint(json.load(open(sys.argv[1]))['body'])\n",
        payload_name="payload.json",
        payload=payload,
    )

    assert Path(result["script_path"]).exists()
    assert Path(result["payload_path"]).exists()
    assert result["argv"] == ["python3", result["script_path"], result["payload_path"]]
    assert "A & B" not in result["shell_command"]
    assert "&" not in result["shell_command"]



def test_completion_only_from_verified_and_delivery_is_terminal(tmp_path):
    orch = _load_script("gcb_full_orchestrator.py")
    state = orch.create_job("source", "gcb full", 1, root_dir=tmp_path)

    assert orch.completion_payload(state)["ok"] is False

    state = _draft_written(orch, tmp_path)
    state = orch.transition(
        state,
        "VERIFIED",
        verification=_verification(),
        shell_copy_verification=_shell_copy_verification(),
        protocol_clean=True,
    )
    payload = orch.completion_payload(state)

    assert state["draft_id"] == 123
    assert state["selected_shell_post_id"] == 99
    assert state["draft_build_method"] == "build-from-shell"
    assert state["draft_build_manifest_path"]
    assert state["shell_copy_verification"]["ok"] is True
    assert state["protocol_clean"] is True
    assert state["verification_result"]["ok"] is True
    assert payload["ok"] is True
    assert payload["post_id"] == 123
    assert payload["draft_edit_url"] == "https://example/edit/123"

    delivered = orch.transition(state, "DELIVERED")
    assert delivered["phase"] == "DELIVERED"
    assert delivered["terminal_state"] == "DELIVERED"

    unchanged = orch.transition(delivered, "VERIFIED", verification=_verification())
    assert unchanged == delivered


def test_final_verification_requires_copy_style_lint(tmp_path):
    orch = _load_script("gcb_full_orchestrator.py")
    state = _draft_written(orch, tmp_path)

    failed = orch.transition(
        state,
        "VERIFIED",
        verification={"ok": True},
        shell_copy_verification=_shell_copy_verification(),
        protocol_clean=True,
    )

    assert failed["phase"] == "FAILED"
    assert "copy-style-lint-not-passed" in failed["errors"]


def test_final_verification_runs_once(tmp_path):
    orch = _load_script("gcb_full_orchestrator.py")
    state = _draft_written(orch, tmp_path)
    verified = orch.transition(
        state,
        "VERIFIED",
        verification=_verification(),
        shell_copy_verification=_shell_copy_verification(),
        protocol_clean=True,
    )
    failed_second = orch.transition(
        verified,
        "VERIFIED",
        verification=_verification(),
        shell_copy_verification=_shell_copy_verification(),
        protocol_clean=True,
    )

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

    state = orch.transition(state, "MEDIA_READY", actual_image_count=1, media_ids=[1], featured_media_id=1, now_monotonic=120.0)
    written = orch.transition(
        state,
        "DRAFT_WRITTEN",
        post_id=5,
        draft_edit_url="https://example/edit/5",
        selected_shell_post_id=99,
        reference_post_id=99,
        reference_content_sha256="refhash",
        draft_build_method="build-from-shell",
        draft_build_manifest_path=_manifest_path(tmp_path),
    )
    assert orch.timebox_failure_line(written, now_monotonic=281.0) == "No protocol-clean draft exists."


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


def test_draft_written_requires_build_from_shell_manifest(tmp_path):
    orch = _load_script("gcb_full_orchestrator.py")
    state = _package_ready(orch, tmp_path)

    failed = orch.transition(state, "DRAFT_WRITTEN", post_id=123, draft_edit_url="https://example/edit/123")

    assert failed["phase"] == "FAILED"
    assert "draft-build-method-not-build-from-shell" in failed["errors"]
    assert "draft-build-manifest-missing" in failed["errors"]
    assert "selected-shell-missing" in failed["errors"]
    assert failed["protocol_clean"] is False


def test_verified_requires_shell_copy_verification(tmp_path):
    orch = _load_script("gcb_full_orchestrator.py")
    state = _draft_written(orch, tmp_path)

    failed = orch.transition(state, "VERIFIED", verification=_verification())

    assert failed["phase"] == "FAILED"
    assert "shell-copy-verification-not-passed" in failed["errors"]
    assert "protocol-clean-not-proven" in failed["errors"]


def test_completion_payload_requires_protocol_clean(tmp_path):
    orch = _load_script("gcb_full_orchestrator.py")
    state = {
        "phase": "VERIFIED",
        "post_id": 123,
        "draft_id": 123,
        "draft_edit_url": "https://example/edit/123",
        "verification_result": {"ok": True},
        "protocol_clean": False,
    }

    payload = orch.completion_payload(state)

    assert payload == {"ok": False, "error": "no-protocol-clean-draft", "message": "No protocol-clean draft exists."}


def test_manual_hybrid_build_cannot_be_delivered_as_verified(tmp_path):
    orch = _load_script("gcb_full_orchestrator.py")
    state = _draft_written(orch, tmp_path)
    manual_verified = {
        **state,
        "phase": "VERIFIED",
        "verification_result": {"ok": True, "style_lint": {"ok": True}},
        "shell_copy_verification": {"ok": False, "issues": [{"rule_id": "manual-hybrid"}]},
        "protocol_clean": False,
    }

    failed = orch.transition(manual_verified, "DELIVERED")

    assert failed["phase"] == "FAILED"
    assert "delivery-requires-protocol-clean" in failed["errors"]


def test_structural_verification_without_protocol_clean_reports_no_protocol_clean_draft(tmp_path):
    orch = _load_script("gcb_full_orchestrator.py")
    state = _draft_written(orch, tmp_path)
    structurally_ok = {**state, "verification_result": {"ok": True, "style_lint": {"ok": True}}, "protocol_clean": False}

    assert orch.alan_report_line(structurally_ok) == "No protocol-clean draft exists."


def test_report_line_says_no_protocol_clean_draft_for_hybrid_build(tmp_path):
    orch = _load_script("gcb_full_orchestrator.py")
    state = _draft_written(orch, tmp_path)
    hybrid = {
        **state,
        "draft_build_method": "manual-hybrid",
        "shell_copy_verification": {"ok": False, "issues": [{"rule_id": "manifest-method-invalid"}]},
        "protocol_clean": False,
    }

    assert orch.alan_report_line(hybrid) == "No protocol-clean draft exists."


def test_timebox_failure_does_not_soften_protocol_failure(tmp_path):
    orch = _load_script("gcb_full_orchestrator.py")
    state = orch.create_job("source", "you have 1 minutes, gcb full", 1, root_dir=tmp_path, now_monotonic=10.0)
    state = orch.transition(state, "MEDIA_READY", actual_image_count=1, media_ids=[1], featured_media_id=1, now_monotonic=20.0)
    state = orch.transition(
        state,
        "DRAFT_WRITTEN",
        post_id=123,
        draft_edit_url="https://example/edit/123",
        selected_shell_post_id=99,
        reference_post_id=99,
        reference_content_sha256="refhash",
        draft_build_method="build-from-shell",
        draft_build_manifest_path=_manifest_path(tmp_path),
    )

    assert orch.timebox_failure_line(state, now_monotonic=71.0) == "No protocol-clean draft exists."
    assert orch.alan_report_line(state, now_monotonic=71.0) == "No protocol-clean draft exists."
