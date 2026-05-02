from __future__ import annotations

from types import SimpleNamespace


def test_memory_status_reports_capacity_counts_and_noise(monkeypatch, tmp_path, capsys):
    from hermes_cli import memory_setup

    hermes_home = tmp_path / ".hermes"
    memories = hermes_home / "memories"
    memories.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    (hermes_home / "config.yaml").write_text(
        """
memory:
  memory_enabled: true
  user_profile_enabled: true
  memory_char_limit: 100
  user_char_limit: 100
  provider: ''
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (memories / "MEMORY.md").write_text("alpha\n§\nalpha\n§\nbeta", encoding="utf-8")
    (memories / "USER.md").write_text("x" * 80, encoding="utf-8")

    memory_setup.cmd_status(SimpleNamespace())

    out = capsys.readouterr().out
    assert "Built-in:  active" in out
    assert "Provider:  (none — built-in only)" in out
    assert "Provider status: inactive" in out
    assert "Memory entries:  3" in out
    assert "Memory usage:    20% — 20/100 chars" in out
    assert "User entries:    1" in out
    assert "User usage:      80% — 80/100 chars" in out
    assert "Near capacity:   user" in out
    assert "Duplicate/noise risk: memory has duplicate entries" in out
