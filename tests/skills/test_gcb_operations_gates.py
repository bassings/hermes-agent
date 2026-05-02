"""Regression tests for canonical active GCB operation gate scripts."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
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


def test_canonical_gcb_gate_scripts_exist_in_active_operations_skill():
    expected = {
        "gcb_style_lint.py",
        "gcb_anti_ai_gate.py",
        "gcb_copy_lint.py",
        "gcb_wp_draft_guard.py",
    }

    assert expected <= {p.name for p in SCRIPT_DIR.glob("*.py")}


def test_style_lint_flags_current_alan_banned_phrases():
    mod = _load_script("gcb_style_lint.py")

    result = mod.lint_text(
        "For Australia, Aussie drivers get a school-run SUV, which makes sense "
        "from a lived experience point of view.",
        mode="article",
    )

    rule_ids = {issue["rule_id"] for issue in result["issues"]}
    assert {
        "banned-for-australia",
        "banned-aussie-bucket",
        "banned-school-run",
        "banned-which-makes-sense",
        "banned-lived-experience",
    } <= rule_ids
    assert result["ok"] is False


def test_style_lint_flags_mechanical_style_regressions():
    mod = _load_script("gcb_style_lint.py")

    result = mod.lint_text(
        "Very modern. Very clever. The color display uses Unicode leak u00e9; "
        "it also has an em dash — and en dash – plus 😊.",
        mode="article",
    )

    rule_ids = {issue["rule_id"] for issue in result["issues"]}
    assert {
        "unicode-escape-leak",
        "staccato-gemini-rhythm",
        "em-dash",
        "en-dash",
        "semicolon",
        "emoji",
        "americanism-color",
    } <= rule_ids
    assert result["ok"] is False


def test_anti_ai_gate_flags_fake_insight_scaffolding():
    mod = _load_script("gcb_anti_ai_gate.py")

    result = mod.check_text("What matters is the way the vehicle changes everything.")

    assert result["ok"] is False
    assert any(issue["rule_id"] == "fake-insight-label" for issue in result["issues"])


def test_anti_ai_gate_flags_structural_ai_copy_but_exempts_table_cells():
    mod = _load_script("gcb_anti_ai_gate.py")

    text = """
    | Spec | Value |
    | --- | --- |
    | Power | 150kW |

    The broader market remains competitive for Aussie buyers looking for a school-run SUV.
    Price: $40,000. Power: 150kW. Torque: 310Nm. Battery: 70kWh. Range: 450km.
    """
    result = mod.check_text(text)

    rule_ids = {issue["rule_id"] for issue in result["issues"]}
    assert "generic-market-wrap" in rule_ids
    assert "aussie-bucket" in rule_ids
    assert "school-run-default" in rule_ids
    assert "spec-dump-without-editorial-consequence" in rule_ids
    assert result["ok"] is False


def test_copy_lint_requires_subject_terms_in_headline_and_excerpt():
    mod = _load_script("gcb_copy_lint.py")

    result = mod.lint_copy(
        headline="A New SUV Arrives With Big Claims",
        excerpt="A new model arrives with big claims and a long equipment list.",
        subject_terms=["Hyundai", "Ioniq"],
    )

    rule_ids = {issue["rule_id"] for issue in result["issues"]}
    assert "headline-missing-subject-term" in rule_ids
    assert "excerpt-missing-subject-term" in rule_ids
    assert result["ok"] is False


def test_copy_lint_rejects_bland_excerpt_and_unearned_year_headline():
    mod = _load_script("gcb_copy_lint.py")

    result = mod.lint_copy(
        headline="2026 SUV Arrives With Big Claims",
        excerpt="This exciting new model offers style, comfort and performance for everyone.",
        subject_terms=["Hyundai", "Ioniq"],
        story_peg_terms=["N Portal", "community"],
        year_earned=False,
    )

    rule_ids = {issue["rule_id"] for issue in result["issues"]}
    assert "headline-year-not-earned" in rule_ids
    assert "headline-missing-story-peg" in rule_ids
    assert "excerpt-bland-seo-filler" in rule_ids


def test_wp_draft_guard_flags_template_markers_and_missing_shell_bits(tmp_path):
    post = {
        "ID": 123,
        "title": {"raw": "Placeholder Title"},
        "content": {
            "raw": "<!-- wp:post-content /--><p>Placeholder Title</p>"
        },
        "status": "draft",
    }
    path = tmp_path / "post.json"
    path.write_text(json.dumps(post), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(SCRIPT_DIR / "gcb_wp_draft_guard.py"), "verify-post", str(path)],
        text=True,
        capture_output=True,
        check=False,
    )

    payload = json.loads(proc.stdout)
    assert proc.returncode == 1
    assert payload["ok"] is False
    rule_ids = {issue["rule_id"] for issue in payload["issues"]}
    assert "forbidden-template-marker" in rule_ids
    assert "missing-spectra-gallery" in rule_ids
    assert "missing-more-stories" in rule_ids


def test_wp_draft_guard_rejects_duplicate_core_gallery_and_missing_required_video(tmp_path):
    post = {
        "ID": 321,
        "title": {"raw": "Story"},
        "content": {
            "raw": """
            <!-- wp:gallery -->bad<!-- /wp:gallery -->
            <!-- wp:uagb/image-gallery {} /-->
            <!-- wp:uagb/image-gallery {} /-->
            <h2>More Stories</h2>
            """
        },
        "status": "draft",
    }
    path = tmp_path / "post.json"
    path.write_text(json.dumps(post), encoding="utf-8")

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_DIR / "gcb_wp_draft_guard.py"),
            "verify-post",
            str(path),
            "--expected-video-url",
            "https://youtube.com/watch?v=abc123",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    payload = json.loads(proc.stdout)
    rule_ids = {issue["rule_id"] for issue in payload["issues"]}
    assert proc.returncode == 1
    assert "core-gallery-not-spectra" in rule_ids
    assert "duplicate-spectra-gallery" in rule_ids
    assert "missing-required-video" in rule_ids


def test_wp_draft_guard_compare_surfaces_rejects_forbidden_spelling_only_changes(tmp_path):
    before = {
        "title": {"raw": "Original Title"},
        "excerpt": {"raw": "Original excerpt"},
        "featured_media": 1,
        "content": {"raw": "<p>Colour typoo.</p><!-- wp:uagb/image-gallery {} /--><h2>More Stories</h2>"},
    }
    after = {
        "title": {"raw": "Changed Title"},
        "excerpt": {"raw": "Changed excerpt"},
        "featured_media": 2,
        "content": {"raw": "<p>Colour typo.</p><!-- wp:uagb/image-gallery {} /--><h2>More Stories</h2>"},
    }
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    before_path.write_text(json.dumps(before), encoding="utf-8")
    after_path.write_text(json.dumps(after), encoding="utf-8")

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_DIR / "gcb_wp_draft_guard.py"),
            "compare-surfaces",
            str(before_path),
            str(after_path),
            "--mode",
            "spelling_only",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    payload = json.loads(proc.stdout)
    rule_ids = {issue["rule_id"] for issue in payload["issues"]}
    assert proc.returncode == 1
    assert "title-changed" in rule_ids
    assert "excerpt-changed" in rule_ids
    assert "featured-media-changed" in rule_ids


def test_wp_draft_guard_verify_shell_copy_catches_shell_mutation(tmp_path):
    before = {
        "content": {"raw": "<p>Story</p><aside class='subscribe'>Subscribe</aside><h2>More Stories</h2>"},
    }
    after = {
        "content": {"raw": "<p>Story changed</p><h2>More Stories</h2>"},
    }
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    before_path.write_text(json.dumps(before), encoding="utf-8")
    after_path.write_text(json.dumps(after), encoding="utf-8")

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_DIR / "gcb_wp_draft_guard.py"),
            "verify-shell-copy",
            str(before_path),
            str(after_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    payload = json.loads(proc.stdout)
    rule_ids = {issue["rule_id"] for issue in payload["issues"]}
    assert proc.returncode == 1
    assert "subscribe-shell-changed" in rule_ids


def test_wp_draft_guard_build_from_shell_swaps_body_only_and_preserves_shell():
    mod = _load_script("gcb_wp_draft_guard.py")
    reference = {
        "title": {"raw": "Old Story"},
        "excerpt": {"raw": "Old excerpt"},
        "featured_media": 55,
        "content": {
            "raw": (
                "<p>Old holding copy.</p>\n"
                "<!-- wp:uagb/image-gallery {\"ids\":[1,2]} /-->\n"
                "<figure>Video https://youtube.com/watch?v=abc123</figure>\n"
                "<h2>More Stories</h2>"
            )
        },
    }
    story = {
        "headline": "New Story",
        "excerpt": "New excerpt",
        "body": "<p>Approved replacement copy.</p>",
    }

    built = mod.build_from_shell(reference, story)
    raw = built["content"]["raw"]

    assert raw.startswith("<p>Approved replacement copy.</p>")
    assert "Old holding copy" not in raw
    assert "uagb/image-gallery" in raw
    assert "youtube.com/watch?v=abc123" in raw
    assert "More Stories" in raw
    assert built["featured_media"] == 55
