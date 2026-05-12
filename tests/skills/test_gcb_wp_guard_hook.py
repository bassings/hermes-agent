"""Local tests for the live GCB WordPress shell hook guard."""

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
    reason="local GCB hook tests require HERMES_LOCAL_SKILL_TESTS=1",
)

HOOK_PATH = Path.home() / ".hermes" / "agent-hooks" / "gcb-wp-guard.py"


def _load_hook():
    spec = importlib.util.spec_from_file_location("gcb_wp_guard", HOOK_PATH)
    assert spec and spec.loader, f"cannot import {HOOK_PATH}"
    module = importlib.util.module_from_spec(spec)
    sys.modules["gcb_wp_guard"] = module
    spec.loader.exec_module(module)
    return module


def _payload(tool_name: str, tool_input: dict) -> dict:
    return {
        "hook_event_name": "pre_tool_call",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "session_id": "test-session",
        "cwd": "/home/scott/.hermes/hermes-agent",
        "extra": {},
    }


def _decision(tool_name: str, tool_input: dict, *, env: dict[str, str] | None = None) -> dict:
    raw = json.dumps(_payload(tool_name, tool_input))
    proc = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=raw,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, **(env or {})},
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip(), "hook must emit JSON"
    return json.loads(proc.stdout)


def test_hook_file_exists_and_exports_classifier():
    hook = _load_hook()
    assert callable(hook.classify_payload)


@pytest.mark.parametrize(
    "command",
    [
        "ssh gcb 'cd /srv/gaycarboys && wp eval-file /tmp/finish_swap.php'",
        "ssh gcb 'cd /srv/gaycarboys && wp db query < /tmp/update_113103.sql'",
        "ssh gcb 'mysql wordpress -e \"UPDATE wp_posts SET post_content=...\"'",
        "python3 /tmp/finish_swap.php /tmp/finish_payload.json",
    ],
)
def test_guard_blocks_terminal_php_db_and_sql_rescue_paths(command):
    result = _decision("terminal", {"command": command})
    assert result["action"] == "block"
    assert "GCB guard blocked unsafe WordPress path" in result["message"]


def test_guard_blocks_execute_code_smuggling_wp_db_query():
    result = _decision(
        "execute_code",
        {"code": "from hermes_tools import terminal\nterminal(\"ssh gcb 'wp db query UPDATE wp_posts'\")"},
    )
    assert result["action"] == "block"


def test_guard_blocks_media_import_after_copied_draft_context():
    result = _decision(
        "terminal",
        {"command": "cd /tmp/gcb_jobs/job-1 && echo copied_draft_id=113103 && wp media import image.jpg"},
    )
    assert result["action"] == "block"


@pytest.mark.parametrize(
    "command",
    [
        "ssh gcb 'cd /srv/gaycarboys && wp post update 113103 --post_status=draft'",
        "ssh gcb 'cd /srv/gaycarboys && wp post term set 113103 category 682107824 --by=id'",
    ],
)
def test_guard_allows_safe_narrow_wp_update_paths(command):
    assert _decision("terminal", {"command": command}) == {}


def test_guard_allows_explicit_emergency_override():
    result = _decision(
        "terminal",
        {"command": "ssh gcb 'cd /srv/gaycarboys && wp db query < /tmp/update_113103.sql'"},
        env={"HERMES_GCB_EMERGENCY_SALVAGE": "1"},
    )
    assert result == {}
