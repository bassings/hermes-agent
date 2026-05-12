"""Local tests for the GCB safe WordPress copy/swap plugin."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("HERMES_LOCAL_SKILL_TESTS") != "1",
    reason="local GCB plugin tests require HERMES_LOCAL_SKILL_TESTS=1",
)

PLUGIN_PATH = Path.home() / ".hermes" / "plugins" / "gcb-safe-wp" / "__init__.py"


def _load_plugin():
    spec = importlib.util.spec_from_file_location("gcb_safe_wp", PLUGIN_PATH)
    assert spec and spec.loader, f"cannot import {PLUGIN_PATH}"
    module = importlib.util.module_from_spec(spec)
    sys.modules["gcb_safe_wp"] = module
    spec.loader.exec_module(module)
    return module


class FakeRunner:
    def __init__(self, outputs: list[dict] | None = None):
        self.commands: list[str] = []
        self.outputs = list(outputs or [])

    def __call__(self, command: str, *, timeout: int = 30) -> dict:
        self.commands.append(command)
        if self.outputs:
            return self.outputs.pop(0)
        return {"exit_code": 0, "stdout": "{}", "stderr": ""}


def _package(path: Path, payload: dict) -> str:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_safe_copy_swap_dry_run_emits_only_allowed_command_classes(tmp_path):
    plugin = _load_plugin()
    article = _package(tmp_path / "article.json", {"headline": "Safe title", "body": "<p>Fresh copy</p>", "excerpt": "Fresh excerpt"})
    media = _package(tmp_path / "media.json", {"media_ids": [11, 12, 13], "featured_media_id": 11, "verified": True})
    taxonomy = _package(tmp_path / "taxonomy.json", {"categories": [682107824], "tags": [193320], "brands": [682118892]})
    runner = FakeRunner()

    result = plugin.run_copy_swap_fast(
        {
            "draft_id": 113103,
            "article_package_path": article,
            "media_manifest_path": media,
            "taxonomy_package_path": taxonomy,
            "deadline_seconds": 30,
            "dry_run": True,
        },
        runner=runner,
    )

    assert result["ok"] is True
    assert result["post_id"] == 113103
    forbidden = "\n".join(result["remote_calls"])
    assert "wp eval-file" not in forbidden
    assert "wp db query" not in forbidden
    assert "wp media import" not in forbidden
    assert "public-api.wordpress.com" not in forbidden
    assert result["checks"]["single_verification_pass"] is True


def test_safe_copy_swap_fails_closed_when_verification_missing(tmp_path):
    plugin = _load_plugin()
    article = _package(tmp_path / "article.json", {"headline": "Safe title", "body": "<p>Fresh copy</p>"})
    media = _package(tmp_path / "media.json", {"media_ids": [11], "featured_media_id": 11, "verified": True})
    taxonomy = _package(tmp_path / "taxonomy.json", {})
    runner = FakeRunner(outputs=[{"exit_code": 1, "stdout": "", "stderr": "verification failed"}])

    result = plugin.run_copy_swap_fast(
        {
            "draft_id": 113103,
            "article_package_path": article,
            "media_manifest_path": media,
            "taxonomy_package_path": taxonomy,
            "deadline_seconds": 30,
            "dry_run": False,
        },
        runner=runner,
    )

    assert result["ok"] is False
    assert result["failure_line"] == "No verified swap exists."
    assert result["checks"].get("verified") is not True
