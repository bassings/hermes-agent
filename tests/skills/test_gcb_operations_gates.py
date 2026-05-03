"""Regression tests for canonical active GCB operation gate scripts."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import re
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


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_json_sha(payload: dict) -> str:
    return _sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _reference_shell_post() -> dict:
    return {
        "ID": 112713,
        "title": {"raw": "Old Good Shell Story"},
        "slug": "old-good-shell-story",
        "excerpt": {"raw": "Old shell excerpt"},
        "featured_media": 55,
        "content": {
            "raw": (
                "<!-- wp:paragraph --><p>Old reference lead copy that should be replaced completely.</p><!-- /wp:paragraph -->\n"
                "<!-- wp:paragraph --><p>Old reference body paragraph that must not leak into the new draft.</p><!-- /wp:paragraph -->\n"
                "<!-- wp:uagb/image-gallery {\"block_id\":\"gallery-1\",\"ids\":[11,22],\"columns\":3,\"linkTo\":\"media\"} /-->\n"
                "<!-- wp:embed {\"url\":\"https://www.youtube.com/watch?v=abc123\",\"type\":\"video\",\"providerNameSlug\":\"youtube\"} -->\n"
                "<figure class=\"wp-block-embed is-type-video is-provider-youtube\"><div class=\"wp-block-embed__wrapper\">https://www.youtube.com/watch?v=abc123</div></figure>\n"
                "<!-- /wp:embed -->\n"
                "<aside class=\"subscribe\">Subscribe to Good Car Bad Car for weekly updates.</aside>\n"
                "<section class=\"more-stories\"><h2>More Stories</h2><ul><li><a href=\"/old-story-a\">Old related story</a></li><li><a href=\"/old-story-b\">Another old story</a></li></ul></section>"
            )
        },
        "status": "publish",
    }


def _story_package() -> dict:
    return {
        "headline": "New Mazda CX-5 Story",
        "slug": "new-mazda-cx-5-story",
        "excerpt": "Mazda CX-5 gains useful context.",
        "body": (
            "<!-- wp:paragraph --><p>New opening copy for the Mazda story.</p><!-- /wp:paragraph -->\n"
            "<!-- wp:paragraph --><p>The EV context has enough range to stay semantic, not padded.</p><!-- /wp:paragraph -->"
        ),
        "gallery_media_payload": [101, 102],
        "more_stories_links": ["/new-story-a", "/new-story-b"],
        "seo_meta": {"description": "New Mazda CX-5 context"},
        "taxonomy": {"categories": [1], "tags": [2]},
        "featured_media": 77,
    }


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _run_guard(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT_DIR / "gcb_wp_draft_guard.py"), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def _build_shell_manifest(tmp_path: Path, *, story: dict | None = None) -> tuple[dict, dict, dict, Path, Path, Path]:
    reference = _reference_shell_post()
    story_package = story or _story_package()
    reference_path = tmp_path / "reference.json"
    story_path = tmp_path / "story.json"
    draft_path = tmp_path / "draft.json"
    manifest_path = tmp_path / "manifest.json"
    _write_json(reference_path, reference)
    _write_json(story_path, story_package)

    proc = _run_guard(
        "build-from-shell",
        str(reference_path),
        str(story_path),
        "--output",
        str(draft_path),
        "--manifest",
        str(manifest_path),
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert draft_path.exists(), proc.stdout
    assert manifest_path.exists(), proc.stdout
    return (
        reference,
        story_package,
        json.loads(draft_path.read_text(encoding="utf-8")),
        reference_path,
        draft_path,
        manifest_path,
    )


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


def test_style_lint_flags_padding_and_banned_word_regression_from_suzuki_job():
    mod = _load_script("gcb_style_lint.py")

    result = mod.lint_text(
        "The thing is this SUV is at 100 per cent. That is the awkward bit. "
        "The basics are sensible enough. The bigger story is sharp pricing in the release.",
        mode="article",
    )

    rule_ids = {issue["rule_id"] for issue in result["issues"]}
    assert {
        "banned-the-thing-is",
        "banned-thing-vehicle",
        "banned-per-cent",
        "banned-that-is-the-awkward-bit",
        "banned-basics-are-sensible-enough",
        "banned-the-bigger-story",
        "banned-sharp-pricing",
        "banned-release",
    } <= rule_ids
    assert result["ok"] is False


def test_style_lint_matches_documented_banned_word_set():
    mod = _load_script("gcb_style_lint.py")

    result = mod.lint_text(
        "Massive clearly literal release. The story is this is where the sort of "
        "padding starts. The logic is simple enough. That last bit is filler and here is where it gets interesting. "
        "Some might say it is literally fine.",
        mode="article",
    )

    rule_ids = {issue["rule_id"] for issue in result["issues"]}
    assert {
        "banned-massive",
        "banned-clearly",
        "banned-release",
        "banned-the-story-is",
        "banned-this-is-where",
        "banned-the-sort-of",
        "banned-the-logic-is-simple-enough",
        "banned-that-last-bit-is",
        "banned-here-is-where-it-gets-interesting",
        "banned-some-might-say",
        "banned-literally-filler",
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


def test_wp_draft_guard_rejects_final_draft_copy_that_fails_style_lint(tmp_path):
    post = {
        "ID": 777,
        "title": {"raw": "Suzuki e VITARA Pricing"},
        "excerpt": {"raw": "Suzuki e VITARA has pricing."},
        "content": {
            "raw": """
            <!-- wp:paragraph --><p>The basics are sensible enough. It charges to 100 per cent.</p><!-- /wp:paragraph -->
            <!-- wp:uagb/image-gallery {"ids":[1,2]} /-->
            <!-- wp:paragraph --><p><strong>More Stories</strong></p><!-- /wp:paragraph -->
            """
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
    rule_ids = {issue["rule_id"] for issue in payload["issues"]}
    assert proc.returncode == 1
    assert payload["checks"]["style_lint_ok"] is False
    assert "style-lint:banned-basics-are-sensible-enough" in rule_ids
    assert "style-lint:banned-per-cent" in rule_ids


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


def test_build_from_shell_writes_manifest_with_reference_hashes(tmp_path):
    reference, story, draft, _reference_path, _draft_path, manifest_path = _build_shell_manifest(tmp_path)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["method"] == "build-from-shell"
    assert manifest["builder"] == "gcb_wp_draft_guard.py"
    assert manifest["builder_version"] >= 2
    assert manifest["reference_post_id"] == 112713
    assert manifest["reference_content_sha256"] == _sha256_text(reference["content"]["raw"])
    assert manifest["story_package_sha256"] == _canonical_json_sha(story)
    assert manifest["output_content_sha256"] == _sha256_text(draft["content"]["raw"])


def test_build_from_shell_manifest_lists_allowed_surfaces(tmp_path):
    *_unused, manifest_path = _build_shell_manifest(tmp_path)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert {
        "title",
        "slug",
        "excerpt",
        "article_copy",
        "gallery_media_payload",
        "gallery_above_line",
        "more_stories_links",
        "seo_meta",
        "taxonomy",
        "featured_media",
    } <= set(manifest["allowed_surfaces"])


def test_build_from_shell_manifest_hashes_fixed_shell_spans(tmp_path):
    *_unused, manifest_path = _build_shell_manifest(tmp_path)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    spans = manifest["fixed_shell_spans"]
    assert {"standing_video", "subscribe_tail", "gallery_wrapper_canonical", "more_stories_wrapper"} <= set(spans)
    for name in ("standing_video", "subscribe_tail", "gallery_wrapper_canonical", "more_stories_wrapper"):
        assert re.match(r"^sha256:[0-9a-f]{64}$", spans[name]), name


def test_verify_shell_copy_strict_fails_without_manifest(tmp_path):
    reference = _reference_shell_post()
    story = _story_package()
    mod = _load_script("gcb_wp_draft_guard.py")
    draft = mod.build_from_shell(reference, story)
    reference_path = tmp_path / "reference.json"
    draft_path = tmp_path / "draft.json"
    _write_json(reference_path, reference)
    _write_json(draft_path, draft)

    proc = _run_guard("verify-shell-copy", str(reference_path), str(draft_path), "--strict")

    payload = json.loads(proc.stdout)
    assert proc.returncode == 1
    assert "manifest-required" in {issue["rule_id"] for issue in payload["issues"]}


def test_verify_shell_copy_strict_rejects_marker_only_hybrid_payload(tmp_path):
    reference = _reference_shell_post()
    story = _story_package()
    raw = reference["content"]["raw"]
    shell_tail = "<!-- wp:uagb/image-gallery" + raw.split("<!-- wp:uagb/image-gallery", 1)[1]
    draft = {
        **reference,
        "title": {"raw": story["headline"]},
        "excerpt": {"raw": story["excerpt"]},
        "content": {"raw": f"{story['body']}\n\n{shell_tail}"},
    }
    reference_path = tmp_path / "reference.json"
    draft_path = tmp_path / "draft.json"
    manifest_path = tmp_path / "manual-manifest.json"
    _write_json(reference_path, reference)
    _write_json(draft_path, draft)
    _write_json(manifest_path, {"method": "manual-hybrid", "reference_post_id": 112713})

    proc = _run_guard("verify-shell-copy", str(reference_path), str(draft_path), "--manifest", str(manifest_path), "--strict")

    payload = json.loads(proc.stdout)
    assert proc.returncode == 1
    assert "manifest-method-invalid" in {issue["rule_id"] for issue in payload["issues"]}


def test_verify_shell_copy_strict_accepts_manifest_build(tmp_path):
    _reference, _story, _draft, reference_path, draft_path, manifest_path = _build_shell_manifest(tmp_path)

    proc = _run_guard("verify-shell-copy", str(reference_path), str(draft_path), "--manifest", str(manifest_path), "--strict")

    payload = json.loads(proc.stdout)
    assert proc.returncode == 0, payload
    assert payload["ok"] is True
    assert payload["protocol_clean"] is True


def test_verify_shell_copy_strict_rejects_changed_subscribe_tail(tmp_path):
    _reference, _story, draft, reference_path, draft_path, manifest_path = _build_shell_manifest(tmp_path)
    draft["content"]["raw"] = draft["content"]["raw"].replace("weekly updates", "daily updates")
    _write_json(draft_path, draft)

    proc = _run_guard("verify-shell-copy", str(reference_path), str(draft_path), "--manifest", str(manifest_path), "--strict")

    payload = json.loads(proc.stdout)
    assert proc.returncode == 1
    assert "subscribe-shell-changed" in {issue["rule_id"] for issue in payload["issues"]}


def test_verify_shell_copy_strict_rejects_changed_spectra_wrapper(tmp_path):
    _reference, _story, draft, reference_path, draft_path, manifest_path = _build_shell_manifest(tmp_path)
    draft["content"]["raw"] = draft["content"]["raw"].replace('"columns":3', '"columns":4')
    _write_json(draft_path, draft)

    proc = _run_guard("verify-shell-copy", str(reference_path), str(draft_path), "--manifest", str(manifest_path), "--strict")

    payload = json.loads(proc.stdout)
    assert proc.returncode == 1
    assert "spectra-gallery-shell-changed" in {issue["rule_id"] for issue in payload["issues"]}


def test_verify_shell_copy_strict_rejects_old_reference_body_leftover(tmp_path):
    _reference, _story, draft, reference_path, draft_path, manifest_path = _build_shell_manifest(tmp_path)
    draft["content"]["raw"] = (
        "<!-- wp:paragraph --><p>Old reference lead copy that should be replaced completely.</p><!-- /wp:paragraph -->\n"
        + draft["content"]["raw"]
    )
    _write_json(draft_path, draft)

    proc = _run_guard("verify-shell-copy", str(reference_path), str(draft_path), "--manifest", str(manifest_path), "--strict")

    payload = json.loads(proc.stdout)
    assert proc.returncode == 1
    assert "old-reference-story-copy-leftover" in {issue["rule_id"] for issue in payload["issues"]}


def test_style_lint_rejects_lovely_enough_padding():
    mod = _load_script("gcb_style_lint.py")

    result = mod.lint_text("The public-facing picture is lovely enough.", mode="article")

    assert result["ok"] is False
    assert "banned-lazy-enough-padding" in {issue["rule_id"] for issue in result["issues"]}


def test_style_lint_rejects_soft_enough_cluster():
    mod = _load_script("gcb_style_lint.py")

    result = mod.lint_text(
        "The cabin is simple enough. The layout is clear enough. "
        "The price looks reasonable enough and the packaging is neat enough. "
        "The answer sounds good enough.",
        mode="article",
    )

    matches = {issue["matched_text"].lower() for issue in result["issues"] if issue["rule_id"] == "banned-lazy-enough-padding"}
    assert {"simple enough", "clear enough", "reasonable enough", "neat enough", "good enough"} <= matches


def test_style_lint_allows_semantic_enough_usage():
    mod = _load_script("gcb_style_lint.py")

    result = mod.lint_text(
        "There is not enough range for that route. The motor has enough torque to tow. "
        "There is enough evidence in the numbers, enough room for luggage and enough charge to get home.",
        mode="article",
    )

    assert result["ok"] is True
    assert not result["issues"]
