"""User-facing gateway status wording must not expose internal machinery."""

from types import SimpleNamespace

from gateway.run import (
    _classify_busy_ack_audience,
    _format_busy_ack,
    _format_gateway_inactivity_timeout_response,
    _format_gateway_inactivity_warning,
    _format_gateway_long_running_notice,
)


INTERNAL_FRAGMENTS = (
    "iteration",
    "terminal",
    "execute_code",
    "tool",
    "api response",
    "context window",
    "token",
    "config.yaml",
    "gateway_timeout",
    "receiving stream response",
)


def _assert_user_safe(text: str) -> None:
    lower = text.lower()
    for fragment in INTERNAL_FRAGMENTS:
        assert fragment not in lower


def _event(text: str = "", user_name: str = "Alan Zurvas"):
    return SimpleNamespace(
        text=text,
        source=SimpleNamespace(user_name=user_name, user_id="u1", chat_id="c1", thread_id=None),
    )


def test_gcb_busy_ack_hides_internals_and_suppresses_onboarding_content():
    text = _format_busy_ack(
        _event("gcb full use the last 9 images"),
        mode="queue",
        summary={"api_call_count": 17, "max_iterations": 90, "current_tool": "terminal"},
        audience="gcb",
    )

    assert text == "Working on the current draft. Your latest message is queued."
    _assert_user_safe(text)
    assert "busy_input" not in text.lower()


def test_scott_busy_ack_keeps_technical_status_for_diagnostics():
    text = _format_busy_ack(
        _event("status", user_name="Scott"),
        mode="interrupt",
        summary={"api_call_count": 4, "max_iterations": 90, "current_tool": "terminal", "elapsed_min": 2},
        audience="technical",
    )

    assert "Interrupting current task" in text
    assert "iteration 4/90" in text
    assert "running: terminal" in text
    assert "2 min elapsed" in text


def test_busy_audience_classifier_detects_gcb_mode_and_scott():
    assert _classify_busy_ack_audience(_event("gcb full this"), session_key="telegram:dm:alan") == "gcb"
    assert _classify_busy_ack_audience(_event("status please", user_name="Scott Bassingthwaighte-Hatch"), session_key="telegram:dm:scott") == "technical"


def test_long_running_notice_hides_iterations_tools_and_activity_desc():
    text = _format_gateway_long_running_notice(
        elapsed_mins=10,
        activity={
            "api_call_count": 16,
            "max_iterations": 150,
            "current_tool": "terminal",
            "last_activity_desc": "receiving stream response",
        },
    )

    assert text == "Still working. I’ll send the result when it’s ready."
    _assert_user_safe(text)


def test_inactivity_warning_is_user_safe():
    text = _format_gateway_inactivity_warning(elapsed_mins=15, remaining_mins=15)

    assert "Still waiting" in text
    _assert_user_safe(text)


def test_inactivity_timeout_response_is_user_safe_but_logs_keep_details_elsewhere():
    text = _format_gateway_inactivity_timeout_response(
        timeout_mins=30,
        activity={
            "last_activity_desc": "receiving stream response",
            "seconds_since_activity": 1800,
            "current_tool": "terminal",
            "api_call_count": 20,
            "max_iterations": 90,
        },
    )

    assert "No verified result was produced" in text
    _assert_user_safe(text)
