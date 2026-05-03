import pytest

import hermes_cli.gateway as gateway_cli


def test_systemd_service_template_uses_short_restart_sec(monkeypatch):
    monkeypatch.delenv("HERMES_GATEWAY_SERVICE_RESTART_SEC", raising=False)
    unit = gateway_cli.generate_systemd_unit()

    assert "RestartSec=3" in unit
    assert "RestartSec=60" not in unit
    assert "RestartForceExitStatus=75" in unit


def test_systemd_service_template_restart_sec_is_configurable(monkeypatch):
    monkeypatch.setenv("HERMES_GATEWAY_SERVICE_RESTART_SEC", "7")

    unit = gateway_cli.generate_systemd_unit()

    assert "RestartSec=7" in unit


def test_gateway_restart_wait_logic_reads_restart_usec(monkeypatch):
    calls = [
        {"ActiveState": "activating", "SubState": "auto-restart", "RestartUSec": "500ms"},
        {"ActiveState": "active", "SubState": "running", "RestartUSec": "500ms"},
    ]
    sleeps = []
    now = {"value": 1000.0}

    def fake_props(system=False):
        return calls.pop(0)

    monkeypatch.setattr(gateway_cli, "_read_systemd_unit_properties", fake_props)
    monkeypatch.setattr(gateway_cli, "get_service_name", lambda: "hermes-gateway.service")
    monkeypatch.setattr(gateway_cli, "_service_scope_label", lambda _system: "user")
    monkeypatch.setattr(gateway_cli, "_select_systemd_scope", lambda system=False: system)
    monkeypatch.setattr("gateway.status.get_running_pid", lambda: 12345)

    import time

    monkeypatch.setattr(time, "time", lambda: now["value"])

    def fake_sleep(seconds):
        sleeps.append(seconds)
        now["value"] += seconds

    monkeypatch.setattr(time, "sleep", fake_sleep)

    assert gateway_cli._wait_for_systemd_service_restart(previous_pid=None, timeout=5) is True
    assert sleeps == [pytest.approx(0.5)]
