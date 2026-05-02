"""Tests for the auto-continue feature (#4493).

When the gateway restarts mid-agent-work, the session transcript ends on a
tool result that the agent never processed.  The auto-continue logic detects
this and prepends a system note to the next user message so the model
finishes the interrupted work before addressing the new input.
"""

import pytest


def _simulate_auto_continue(agent_history: list, user_message: str) -> str:
    """Reproduce the auto-continue injection logic from _run_agent().

    This mirrors the exact code in gateway/run.py so we can test the
    detection and message transformation without spinning up a full
    gateway runner.
    """
    from gateway.run import _is_resume_note_override_message

    message = user_message
    if (
        agent_history
        and agent_history[-1].get("role") == "tool"
        and not _is_resume_note_override_message(user_message)
    ):
        message = (
            "[System note: Your previous turn was interrupted before you could "
            "process the last tool result(s). The conversation history contains "
            "tool outputs you haven't responded to yet. Please finish processing "
            "those results and summarize what was accomplished, then address the "
            "user's new message below.]\n\n"
            + message
        )
    return message


class TestAutoDetection:
    """Test that trailing tool results are correctly detected."""

    def test_trailing_tool_result_triggers_note(self):
        history = [
            {"role": "user", "content": "deploy the app"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "function": {"name": "terminal", "arguments": "{}"}}
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": "deployed successfully"},
        ]
        result = _simulate_auto_continue(history, "what happened?")
        assert "[System note:" in result
        assert "interrupted" in result
        assert "what happened?" in result

    def test_trailing_assistant_message_no_note(self):
        history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        result = _simulate_auto_continue(history, "how are you?")
        assert "[System note:" not in result
        assert result == "how are you?"

    def test_empty_history_no_note(self):
        result = _simulate_auto_continue([], "hello")
        assert result == "hello"

    def test_trailing_user_message_no_note(self):
        """Shouldn't happen in practice, but ensure no false positive."""
        history = [
            {"role": "user", "content": "hello"},
        ]
        result = _simulate_auto_continue(history, "hello again")
        assert result == "hello again"

    def test_multiple_tool_results_still_triggers(self):
        """Multiple tool calls in a row — last one is still role=tool."""
        history = [
            {"role": "user", "content": "search and read"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "function": {"name": "search", "arguments": "{}"}},
                {"id": "call_2", "function": {"name": "read", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": "found it"},
            {"role": "tool", "tool_call_id": "call_2", "content": "file content here"},
        ]
        result = _simulate_auto_continue(history, "continue")
        assert "[System note:" in result

    def test_original_message_preserved_after_note(self):
        """The user's actual message must appear after the system note."""
        history = [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "function": {"name": "t", "arguments": "{}"}}
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "done"},
        ]
        result = _simulate_auto_continue(history, "now do X")
        # System note comes first, then user's message
        note_end = result.index("]\n\n")
        user_msg_start = result.index("now do X")
        assert user_msg_start > note_end

    @pytest.mark.parametrize(
        "user_message",
        [
            "stop",
            "stop now",
            "what are you doing?",
            "who mention Bugatti?",
            "that is several requests ago",
            "wrong story",
            "new request",
            "source: fresh supplied text for a different story",
            "use the last 9 images",
            "gcb full use the last 9 images",
            "full build this from the supplied text",
        ],
    )
    def test_latest_explicit_override_suppresses_stale_tool_tail_note(self, user_message):
        history = [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "function": {"name": "terminal", "arguments": "{}"}}
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "old task output"},
        ]

        result = _simulate_auto_continue(history, user_message)

        assert "[System note:" not in result
        assert result == user_message

    def test_normal_follow_up_still_gets_tool_tail_note(self):
        history = [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "function": {"name": "terminal", "arguments": "{}"}}
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "current task output"},
        ]

        result = _simulate_auto_continue(history, "what happened?")

        assert "[System note:" in result
        assert "what happened?" in result


class TestCleanSessionBoundaries:
    """Latest live-job boundary messages should start from a clean context."""

    @pytest.mark.parametrize(
        ("user_message", "expected_reason"),
        [
            ("gcb full 0 images Source: new source text", "gcb_full"),
            ("full build this from the supplied text", "full_build"),
            ("new story", "new_work"),
            ("wrong story", "stale_context_correction"),
            ("that is several requests ago", "stale_context_correction"),
            ("I edited the draft manually", "manual_edit"),
            ("The draft failed, start again", "failed_live_edit"),
            ("the update timed out", "failed_live_edit"),
            ("stop", "stop"),
        ],
    )
    def test_live_dirty_session_messages_are_clean_boundaries(self, user_message, expected_reason):
        from gateway.run import _clean_session_boundary_reason

        assert _clean_session_boundary_reason(user_message) == expected_reason

    @pytest.mark.parametrize(
        "user_message",
        [
            "what happened?",
            "continue",
            "resume the current job",
            "can you report status?",
            "fix the typo in the third paragraph",
        ],
    )
    def test_normal_followups_are_not_clean_boundaries(self, user_message):
        from gateway.run import _clean_session_boundary_reason

        assert _clean_session_boundary_reason(user_message) is None
