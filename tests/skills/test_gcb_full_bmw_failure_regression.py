"""BMW regression tests for hardening the local GCB full mock pipeline.

These tests are deterministic and local-only. They encode the two-millionth EV
failure modes so production code can be implemented TDD-first after RED.
"""

from __future__ import annotations

import importlib.util
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


def _load_gcb_modules():
    return (
        _load_script("gcb_full_orchestrator.py"),
        _load_script("gcb_wp_draft_guard.py"),
        _load_script("gcb_style_lint.py"),
    )


DENZA_VIDEO_URL = "https://youtu.be/JWZPDJsd5CA"
STALE_LOCAL_VIDEO_URL = "https://youtu.be/wrong-byd-sealion-8"


def _shell_raw(video_url: str, *, more_story_titles: list[str] | None = None) -> str:
    titles = more_story_titles or [
        "BMW two-millionth EV milestone context",
        "Denza electric-car market update",
    ]
    items = "".join(f'<li><a href="/{idx}">{title}</a></li>' for idx, title in enumerate(titles, 1))
    return (
        "<!-- wp:paragraph --><p>Reference shell copy that must be replaced.</p><!-- /wp:paragraph -->\n"
        "<!-- wp:uagb/image-gallery {\"ids\":[101,102],\"columns\":3} /-->\n"
        f"<!-- wp:embed {{\"url\":\"{video_url}\",\"type\":\"video\",\"providerNameSlug\":\"youtube\"}} -->\n"
        f'<figure class="wp-block-embed is-type-video is-provider-youtube"><div class="wp-block-embed__wrapper">{video_url}</div></figure>\n'
        "<!-- /wp:embed -->\n"
        '<aside class="subscribe">Subscribe to Good Car Bad Car for weekly updates.</aside>\n'
        f'<section class="more-stories"><h2>More Stories</h2><ul>{items}</ul></section>'
    )


def _published_shell_posts() -> list[dict]:
    return [
        {
            "ID": 112800,
            "id": 112800,
            "status": "publish",
            "date_gmt": "2026-05-01T08:00:00",
            "date": "2026-05-01T18:00:00",
            "title": {"raw": "Older BMW EV Shell"},
            "slug": "older-bmw-ev-shell",
            "video_url": "https://youtu.be/older-bmw-shell",
            "content": {"raw": _shell_raw("https://youtu.be/older-bmw-shell")},
        },
        {
            "ID": 112926,
            "id": 112926,
            "status": "publish",
            "date_gmt": "2026-05-09T10:30:00",
            "date": "2026-05-09T20:30:00",
            "title": {"raw": "Denza Shell With The Correct Standing Video"},
            "slug": "denza-shell-correct-standing-video",
            "video_url": DENZA_VIDEO_URL,
            "content": {
                "raw": _shell_raw(
                    DENZA_VIDEO_URL,
                    more_story_titles=[
                        "BMW two-millionth EV arrives with production context",
                        "Denza electric-car growth keeps pressure on Europe",
                    ],
                )
            },
        },
    ]


def _stale_local_shell_candidate() -> dict:
    return {
        "ID": 112777,
        "id": 112777,
        "status": "draft",
        "date_gmt": "2026-04-01T00:00:00",
        "date": "2026-04-01T10:00:00",
        "title": {"raw": "Mercedes-Benz CLA Local Shell With BYD Video"},
        "slug": "mercedes-benz-cla-local-shell-byd-video",
        "video_url": STALE_LOCAL_VIDEO_URL,
        "content": {
            "raw": _shell_raw(
                STALE_LOCAL_VIDEO_URL,
                more_story_titles=[
                    "Mercedes-Benz CLA launches with local context",
                    "BYD Sealion 8 video guide",
                ],
            )
        },
    }


def _bmw_pipeline_kwargs(tmp_path: Path) -> dict:
    return {
        "source_text": "BMW has built its two-millionth fully electric car and wants the milestone copy handled cleanly.",
        "latest_user_message": "gcb full 3 images",
        "expected_image_count": 3,
        "story_slug": "bmw-two-millionth-ev-regression",
        "root_dir": tmp_path,
        "media_ids": [112901, 112902, 112903],
        "body_text": "<!-- wp:paragraph --><p>BMW reaches its two-millionth EV with production context, not shell drift.</p><!-- /wp:paragraph -->",
    }


def test_media_ready_forbids_detours_before_copy(tmp_path):
    orch, _guard, _style_lint = _load_gcb_modules()

    result = orch.run_mock_pipeline(
        **_bmw_pipeline_kwargs(tmp_path),
        post_media_detours=["guard.inspect", "taxonomy.probe"],
    )

    state = result["state"]
    assert result["ok"] is False
    assert state["phase"] == "FAILED"
    assert "post-media-detour-before-copy" in state["errors"]
    assert "wp.create_draft" not in result["operations"]
    assert "copy.ready" not in result["operations"]


def test_post_copy_forbidden_detours_fail_closed(tmp_path):
    orch, _guard, _style_lint = _load_gcb_modules()

    result = orch.run_mock_pipeline(
        **_bmw_pipeline_kwargs(tmp_path),
        post_copy_detours=["wp.eval_file", "wp.db_query", "wp.media_import"],
    )

    state = result["state"]
    assert result["ok"] is False
    assert state["phase"] == "FAILED"
    assert "forbidden-post-copy-detour" in state["errors"]
    assert state.get("previous_post_copied_visible") is True
    assert result["completion_payload"].get("ok") is not True
    assert orch.alan_report_line(state) == "No verified swap exists."
    assert "verify.once" not in result["operations"]


def test_previous_post_copied_visible_phase_records_copy_proof(tmp_path):
    orch, _guard, _style_lint = _load_gcb_modules()
    state = orch.create_job(
        "source",
        "gcb full 1 image",
        1,
        story_slug="copy-visible-regression",
        root_dir=tmp_path,
        now_monotonic=0.0,
    )
    state = orch.transition(state, "MEDIA_READY", actual_image_count=1, media_ids=[10], featured_media_id=10, now_monotonic=10.0)

    state = orch.transition(
        state,
        "PREVIOUS_POST_COPIED_VISIBLE",
        post_id=113103,
        draft_edit_url="https://mock.gcb/wp-admin/post.php?post=113103&action=edit",
        selected_shell_post_id=112926,
        reference_post_id=112926,
    )

    assert state["phase"] == "PREVIOUS_POST_COPIED_VISIBLE"
    assert state["post_id"] == 113103
    assert state["previous_post_copied_visible"] is True
    assert state["selected_shell_post_id"] == 112926


def test_latest_published_shell_beats_stale_local_shell(tmp_path):
    orch, _guard, _style_lint = _load_gcb_modules()
    stale = _stale_local_shell_candidate()

    result = orch.run_mock_pipeline(
        **_bmw_pipeline_kwargs(tmp_path),
        published_shell_posts=_published_shell_posts(),
        local_shell_candidate=stale,
    )

    state = result["state"]
    assert result["ok"] is True
    assert result["completion_payload"]["ok"] is True
    assert state.get("selected_shell_post_id") == 112926
    assert state.get("reference_post_id") == 112926
    assert state.get("selected_shell_video_url") == DENZA_VIDEO_URL
    assert state.get("selected_shell_video_url") != stale["video_url"]
    assert state.get("selected_shell_title") != stale["title"]["raw"]


def test_wrong_inherited_video_fails_verification(tmp_path):
    orch, _guard, _style_lint = _load_gcb_modules()

    result = orch.run_mock_pipeline(
        **_bmw_pipeline_kwargs(tmp_path),
        published_shell_posts=_published_shell_posts(),
        final_video_url=STALE_LOCAL_VIDEO_URL,
    )

    state = result["state"]
    assert result["ok"] is False
    assert state["phase"] == "FAILED"
    assert "standing-video-mismatch" in state["errors"]
    assert result["completion_payload"].get("ok") is not True


def test_old_reference_body_after_gallery_fails_protocol_clean(tmp_path):
    orch, _guard, _style_lint = _load_gcb_modules()
    final_contamination = (
        "<!-- wp:paragraph --><p>Fresh BMW EV copy above the gallery.</p><!-- /wp:paragraph -->\n"
        "<!-- wp:uagb/image-gallery {\"ids\":[112901,112902,112903],\"columns\":3} /-->\n"
        "<!-- wp:paragraph --><p>Prices are MRLP and Mercedes-Benz CLA details belong to the old reference body.</p><!-- /wp:paragraph -->"
    )

    result = orch.run_mock_pipeline(
        **_bmw_pipeline_kwargs(tmp_path),
        published_shell_posts=_published_shell_posts(),
        final_contamination_text=final_contamination,
    )

    state = result["state"]
    assert result["ok"] is False
    assert state["phase"] == "FAILED"
    assert "old-reference-body-contamination" in state["errors"]
    assert "wp.create_draft" in result["operations"]
    assert result["completion_payload"].get("ok") is not True


def test_wrong_model_more_stories_fail_or_are_explicitly_reported(tmp_path):
    orch, _guard, _style_lint = _load_gcb_modules()

    result = orch.run_mock_pipeline(
        **_bmw_pipeline_kwargs(tmp_path),
        published_shell_posts=_published_shell_posts(),
        final_more_stories_terms=[
            "Mercedes-Benz CLA electric sedan pricing explained",
            "Mercedes-AMG CLA battery tech details",
        ],
    )

    state = result["state"]
    assert result["ok"] is False
    assert state["phase"] == "FAILED"
    assert "more-stories-context-mismatch" in state["errors"]
    assert result["completion_payload"].get("ok") is not True


def test_bmw_scaffold_copy_phrases_are_linted():
    _orch, _guard, style_lint = _load_gcb_modules()

    result = style_lint.lint_text(
        "The number matters because BMW wants the milestone to do more than decorate the lead. "
        "That ordinariness is the point when the two-millionth EV becomes routine.",
        mode="article",
    )

    rule_ids = {issue["rule_id"] for issue in result["issues"]}
    assert "banned-number-matters-because" in rule_ids
    assert "banned-ordinariness-is-the-point" in rule_ids
    assert result["ok"] is False
