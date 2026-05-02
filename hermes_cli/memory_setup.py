"""hermes memory setup|status — configure memory provider plugins.

Auto-detects installed memory providers via the plugin system.
Interactive curses-based UI for provider selection, then walks through
the provider's config schema. Writes config to config.yaml + .env.
"""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

from hermes_constants import get_hermes_home


# ---------------------------------------------------------------------------
# Curses-based interactive picker (same pattern as hermes tools)
# ---------------------------------------------------------------------------

def _curses_select(title: str, items: list[tuple[str, str]], default: int = 0) -> int:
    """Interactive single-select with arrow keys.

    items: list of (label, description) tuples.
    Returns selected index, or default on escape/quit.
    """
    from hermes_cli.curses_ui import curses_radiolist
    # Format (label, desc) tuples into display strings
    display_items = [
        f"{label}  {desc}" if desc else label
        for label, desc in items
    ]
    return curses_radiolist(title, display_items, selected=default, cancel_returns=default)


def _prompt(label: str, default: str | None = None, secret: bool = False) -> str:
    """Prompt for a value with optional default and secret masking."""
    suffix = f" [{default}]" if default else ""
    if secret:
        sys.stdout.write(f"  {label}{suffix}: ")
        sys.stdout.flush()
        if sys.stdin.isatty():
            val = getpass.getpass(prompt="")
        else:
            val = sys.stdin.readline().strip()
    else:
        sys.stdout.write(f"  {label}{suffix}: ")
        sys.stdout.flush()
        val = sys.stdin.readline().strip()
    return val or (default or "")


# ---------------------------------------------------------------------------
# Provider discovery
# ---------------------------------------------------------------------------

def _install_dependencies(provider_name: str) -> None:
    """Install pip dependencies declared in plugin.yaml."""
    import subprocess
    from plugins.memory import find_provider_dir

    plugin_dir = find_provider_dir(provider_name)
    if not plugin_dir:
        return
    yaml_path = plugin_dir / "plugin.yaml"
    if not yaml_path.exists():
        return

    try:
        import yaml
        with open(yaml_path) as f:
            meta = yaml.safe_load(f) or {}
    except Exception:
        return

    pip_deps = meta.get("pip_dependencies", [])
    if not pip_deps:
        return

    # pip name → import name mapping for packages where they differ
    _IMPORT_NAMES = {
        "honcho-ai": "honcho",
        "mem0ai": "mem0",
        "hindsight-client": "hindsight_client",
        "hindsight-all": "hindsight",
    }

    # Check which packages are missing
    missing = []
    for dep in pip_deps:
        import_name = _IMPORT_NAMES.get(dep, dep.replace("-", "_").split("[")[0])
        try:
            __import__(import_name)
        except ImportError:
            missing.append(dep)

    if not missing:
        return

    print(f"\n  Installing dependencies: {', '.join(missing)}")

    import shutil
    uv_path = shutil.which("uv")
    if not uv_path:
        print(f"  ⚠ uv not found — cannot install dependencies")
        print(f"  Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh")
        print(f"  Then re-run: hermes memory setup")
        return

    try:
        subprocess.run(
            [uv_path, "pip", "install", "--python", sys.executable, "--quiet"] + missing,
            check=True, timeout=120,
            capture_output=True,
        )
        print(f"  ✓ Installed {', '.join(missing)}")
    except subprocess.CalledProcessError as e:
        print(f"  ⚠ Failed to install {', '.join(missing)}")
        stderr = (e.stderr or b"").decode()[:200]
        if stderr:
            print(f"    {stderr}")
        print(f"  Run manually: uv pip install --python {sys.executable} {' '.join(missing)}")
    except Exception as e:
        print(f"  ⚠ Install failed: {e}")
        print(f"  Run manually: uv pip install --python {sys.executable} {' '.join(missing)}")

    # Also show external dependencies (non-pip) if any
    ext_deps = meta.get("external_dependencies", [])
    for dep in ext_deps:
        dep_name = dep.get("name", "")
        check_cmd = dep.get("check", "")
        install_cmd = dep.get("install", "")
        if check_cmd:
            try:
                subprocess.run(
                    check_cmd, shell=True, capture_output=True, timeout=5
                )
            except Exception:
                if install_cmd:
                    print(f"\n  ⚠ '{dep_name}' not found. Install with:")
                    print(f"    {install_cmd}")


def _get_available_providers() -> list:
    """Discover memory providers from plugins/memory/.

    Returns list of (name, description, provider_instance) tuples.
    """
    try:
        from plugins.memory import discover_memory_providers, load_memory_provider
        raw = discover_memory_providers()
    except Exception:
        raw = []

    results = []
    for name, desc, available in raw:
        try:
            provider = load_memory_provider(name)
            if not provider:
                continue
        except Exception:
            continue

        schema = provider.get_config_schema() if hasattr(provider, "get_config_schema") else []
        has_secrets = any(f.get("secret") for f in schema)
        has_non_secrets = any(not f.get("secret") for f in schema)
        if has_secrets and has_non_secrets:
            setup_hint = "API key / local"
        elif has_secrets:
            setup_hint = "requires API key"
        elif not schema:
            setup_hint = "no setup needed"
        else:
            setup_hint = "local"

        results.append((name, setup_hint, provider))
    return results


# ---------------------------------------------------------------------------
# Setup wizard
# ---------------------------------------------------------------------------

def cmd_setup_provider(provider_name: str) -> None:
    """Run memory setup for a specific provider, skipping the picker."""
    from hermes_cli.config import load_config, save_config

    providers = _get_available_providers()
    match = None
    for name, desc, provider in providers:
        if name == provider_name:
            match = (name, desc, provider)
            break

    if not match:
        print(f"\n  Memory provider '{provider_name}' not found.")
        print("  Run 'hermes memory setup' to see available providers.\n")
        return

    name, _, provider = match

    _install_dependencies(name)

    config = load_config()
    if not isinstance(config.get("memory"), dict):
        config["memory"] = {}

    if hasattr(provider, "post_setup"):
        hermes_home = str(get_hermes_home())
        provider.post_setup(hermes_home, config)
        return

    # Fallback: generic schema-based setup (same as cmd_setup)
    config["memory"]["provider"] = name
    save_config(config)
    print(f"\n  Memory provider: {name}")
    print(f"  Activation saved to config.yaml\n")


def cmd_setup(args) -> None:
    """Interactive memory provider setup wizard."""
    from hermes_cli.config import load_config, save_config

    providers = _get_available_providers()

    if not providers:
        print("\n  No memory provider plugins detected.")
        print("  Install a plugin to ~/.hermes/plugins/ and try again.\n")
        return

    # Build picker items
    items = []
    for name, desc, _ in providers:
        items.append((name, f"— {desc}"))
    items.append(("Built-in only", "— MEMORY.md / USER.md (default)"))

    builtin_idx = len(items) - 1
    selected = _curses_select("Memory provider setup", items, default=builtin_idx)

    config = load_config()
    if not isinstance(config.get("memory"), dict):
        config["memory"] = {}

    # Built-in only
    if selected >= len(providers) or selected < 0:
        config["memory"]["provider"] = ""
        save_config(config)
        print("\n  ✓ Memory provider: built-in only")
        print("  Saved to config.yaml\n")
        return

    name, _, provider = providers[selected]

    # Install pip dependencies if declared in plugin.yaml
    _install_dependencies(name)

    # If the provider has a post_setup hook, delegate entirely to it.
    # The hook handles its own config, connection test, and activation.
    if hasattr(provider, "post_setup"):
        hermes_home = str(get_hermes_home())
        provider.post_setup(hermes_home, config)
        return

    schema = provider.get_config_schema() if hasattr(provider, "get_config_schema") else []

    provider_config = config["memory"].get(name, {})
    if not isinstance(provider_config, dict):
        provider_config = {}

    env_path = get_hermes_home() / ".env"
    env_writes = {}

    if schema:
        print(f"\n  Configuring {name}:\n")

        for field in schema:
            key = field["key"]
            desc = field.get("description", key)
            default = field.get("default")
            # Dynamic default: look up default from another field's value
            default_from = field.get("default_from")
            if default_from and isinstance(default_from, dict):
                ref_field = default_from.get("field", "")
                ref_map = default_from.get("map", {})
                ref_value = provider_config.get(ref_field, "")
                if ref_value and ref_value in ref_map:
                    default = ref_map[ref_value]
            is_secret = field.get("secret", False)
            choices = field.get("choices")
            env_var = field.get("env_var")
            url = field.get("url")

            # Skip fields whose "when" condition doesn't match
            when = field.get("when")
            if when and isinstance(when, dict):
                if not all(provider_config.get(k) == v for k, v in when.items()):
                    continue

            if choices and not is_secret:
                # Use curses picker for choice fields
                choice_items = [(c, "") for c in choices]
                current = provider_config.get(key, default)
                current_idx = 0
                if current and current in choices:
                    current_idx = choices.index(current)
                sel = _curses_select(f"  {desc}", choice_items, default=current_idx)
                provider_config[key] = choices[sel]
            elif is_secret:
                # Prompt for secret
                existing = os.environ.get(env_var, "") if env_var else ""
                if existing:
                    masked = f"...{existing[-4:]}" if len(existing) > 4 else "set"
                    val = _prompt(f"{desc} (current: {masked}, blank to keep)", secret=True)
                else:
                    hint = f"  Get yours at {url}" if url else ""
                    if hint:
                        print(hint)
                    val = _prompt(desc, secret=True)
                if val and env_var:
                    env_writes[env_var] = val
            else:
                # Regular text prompt
                current = provider_config.get(key)
                effective_default = current or default
                val = _prompt(desc, default=str(effective_default) if effective_default else None)
                if val:
                    provider_config[key] = val
                    # Also write to .env if this field has an env_var
                    if env_var and env_var not in env_writes:
                        env_writes[env_var] = val

    # Write activation key to config.yaml
    config["memory"]["provider"] = name
    save_config(config)

    # Write non-secret config to provider's native location
    hermes_home = str(get_hermes_home())
    if provider_config and hasattr(provider, "save_config"):
        try:
            provider.save_config(provider_config, hermes_home)
        except Exception as e:
            print(f"  Failed to write provider config: {e}")

    # Write secrets to .env
    if env_writes:
        _write_env_vars(env_path, env_writes)

    print(f"\n  Memory provider: {name}")
    print(f"  Activation saved to config.yaml")
    if provider_config:
        print(f"  Provider config saved")
    if env_writes:
        print(f"  API keys saved to .env")
    print(f"\n  Start a new session to activate.\n")


def _write_env_vars(env_path: Path, env_writes: dict) -> None:
    """Append or update env vars in .env file."""
    env_path.parent.mkdir(parents=True, exist_ok=True)

    existing_lines = []
    if env_path.exists():
        existing_lines = env_path.read_text().splitlines()

    updated_keys = set()
    new_lines = []
    for line in existing_lines:
        key_match = line.split("=", 1)[0].strip() if "=" in line else ""
        if key_match in env_writes:
            new_lines.append(f"{key_match}={env_writes[key_match]}")
            updated_keys.add(key_match)
        else:
            new_lines.append(line)

    for key, val in env_writes.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={val}")

    env_path.write_text("\n".join(new_lines) + "\n")


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

_ENTRY_DELIMITER = "\n§\n"


def _read_raw_memory_entries(path: Path) -> list[str]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    return [part.strip() for part in text.split(_ENTRY_DELIMITER) if part.strip()]


def _memory_usage(entries: list[str], limit: int) -> tuple[int, int]:
    used = len(_ENTRY_DELIMITER.join(entries)) if entries else 0
    pct = int(round((used / limit) * 100)) if limit else 0
    return used, pct


def _duplicate_count(entries: list[str]) -> int:
    seen = set()
    duplicates = 0
    for entry in entries:
        key = " ".join(entry.lower().split())
        if key in seen:
            duplicates += 1
        else:
            seen.add(key)
    return duplicates


def _fact_count(config: dict) -> int:
    try:
        import sqlite3

        plugin_cfg = ((config.get("plugins") or {}).get("hermes-memory-store") or {})
        db_path = str(plugin_cfg.get("db_path") or (get_hermes_home() / "memory_store.db"))
        db_path = db_path.replace("$HERMES_HOME", str(get_hermes_home()))
        db_path = db_path.replace("${HERMES_HOME}", str(get_hermes_home()))
        path = Path(db_path).expanduser()
        if not path.exists():
            return 0
        with sqlite3.connect(path) as conn:
            row = conn.execute("SELECT COUNT(*) FROM facts").fetchone()
            return int(row[0] if row else 0)
    except Exception:
        return 0


def _status_line_for_usage(name: str, entries: list[str], limit: int) -> tuple[list[str], bool]:
    used, pct = _memory_usage(entries, limit)
    lines = [
        f"  {name} entries:  {len(entries)}",
        f"  {name} usage:    {pct}% — {used:,}/{limit:,} chars",
    ]
    return lines, pct >= 75


def cmd_status(args) -> None:
    """Show current memory provider config."""
    from hermes_cli.config import load_config

    config = load_config()
    mem_config = config.get("memory", {}) if isinstance(config.get("memory", {}), dict) else {}
    provider_name = str(mem_config.get("provider", "") or "").strip()
    memory_enabled = bool(mem_config.get("memory_enabled", False))
    user_profile_enabled = bool(mem_config.get("user_profile_enabled", False))
    memory_limit = int(mem_config.get("memory_char_limit", 2200) or 2200)
    user_limit = int(mem_config.get("user_char_limit", 1375) or 1375)

    mem_dir = get_hermes_home() / "memories"
    memory_entries = _read_raw_memory_entries(mem_dir / "MEMORY.md")
    user_entries = _read_raw_memory_entries(mem_dir / "USER.md")
    memory_used, memory_pct = _memory_usage(memory_entries, memory_limit)
    user_used, user_pct = _memory_usage(user_entries, user_limit)
    memory_dupes = _duplicate_count(memory_entries)
    user_dupes = _duplicate_count(user_entries)
    near_capacity = []
    if memory_pct >= 75:
        near_capacity.append("memory")
    if user_pct >= 75:
        near_capacity.append("user")

    providers = _get_available_providers()
    provider_status = "inactive"
    active_provider = None
    if provider_name:
        for pname, _desc, provider in providers:
            if pname == provider_name:
                active_provider = provider
                provider_status = "active" if provider.is_available() else "configured but unavailable"
                break
        else:
            provider_status = "configured but not installed"

    print(f"\nMemory status\n" + "─" * 40)
    print(f"  Built-in:  {'active' if (memory_enabled or user_profile_enabled) else 'inactive'}")
    print(f"  Provider:  {provider_name or '(none — built-in only)'}")
    print(f"  Provider status: {provider_status}")
    print(f"  Memory entries:  {len(memory_entries)}")
    print(f"  Memory usage:    {memory_pct}% — {memory_used:,}/{memory_limit:,} chars")
    print(f"  User entries:    {len(user_entries)}")
    print(f"  User usage:      {user_pct}% — {user_used:,}/{user_limit:,} chars")
    print(f"  Fact count:      {_fact_count(config)}")
    print(f"  Near capacity:   {', '.join(near_capacity) if near_capacity else 'no'}")
    duplicate_notes = []
    if memory_dupes:
        duplicate_notes.append("memory has duplicate entries")
    if user_dupes:
        duplicate_notes.append("user profile has duplicate entries")
    if memory_pct >= 90 or user_pct >= 90:
        duplicate_notes.append("very high usage increases stale/noisy prompt risk")
    print(f"  Duplicate/noise risk: {', '.join(duplicate_notes) if duplicate_notes else 'low'}")

    if provider_name:
        provider_config = mem_config.get(provider_name, {})
        if provider_config:
            print(f"\n  {provider_name} config:")
            for key, val in provider_config.items():
                print(f"    {key}: {val}")

        if active_provider is not None:
            print(f"\n  Plugin:    installed ✓")
            if provider_status == "active":
                print(f"  Status:    available ✓")
            else:
                print(f"  Status:    not available ✗")
                schema = active_provider.get_config_schema() if hasattr(active_provider, "get_config_schema") else []
                required_fields = [f for f in schema if f.get("env_var")]
                if required_fields:
                    print(f"  Missing:")
                    for f in required_fields:
                        env_var = f.get("env_var", "")
                        url = f.get("url", "")
                        is_set = bool(os.environ.get(env_var))
                        mark = "✓" if is_set else "✗"
                        line = f"    {mark} {env_var}"
                        if url and not is_set:
                            line += f"  → {url}"
                        print(line)
        else:
            print(f"\n  Plugin:    NOT installed ✗")
            print(f"  Install the '{provider_name}' memory plugin to ~/.hermes/plugins/")

    if providers:
        print(f"\n  Installed plugins:")
        for pname, desc, _ in providers:
            active = " ← active" if pname == provider_name else ""
            print(f"    • {pname}  ({desc}){active}")

    print()


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def memory_command(args) -> None:
    """Route memory subcommands."""
    sub = getattr(args, "memory_command", None)
    if sub == "setup":
        cmd_setup(args)
    elif sub == "status":
        cmd_status(args)
    else:
        cmd_status(args)
