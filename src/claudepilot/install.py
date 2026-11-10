"""Register / unregister ClaudePilot in Claude Code's settings.json.

- Always writes a timestamped backup first.
- Only touches entries it owns (marked by MARKER in the command string).
- An existing user statusline is preserved: it is saved and chained (ClaudePilot
  runs it and prints its output), and restored on uninstall.
"""

from __future__ import annotations

import json
import shlex
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from claudepilot import state
from claudepilot.paths import claude_settings_path

MARKER = "claudepilot.hooks"
AGENT_MARKER = "managed by claudepilot"
AGENT_NAMES = ("claudepilot-quick", "claudepilot-deep")
HOOK_EVENTS = {
    "UserPromptSubmit": "prompt",
    "PreModelSwitch": "pre-model-switch",
    "PostModelSwitch": "model-switch",
    "PreToolUse": "guard",
}
# Tools whose inputs the secret guard inspects (exact-name matcher list).
MATCHERS = {"PreToolUse": "Read|Edit|Write|MultiEdit|NotebookEdit|Grep|Glob|Bash"}


HOOK_SCRIPT = "claudepilot-hook"


def hook_command(name: str) -> str:
    """Prefer the `claudepilot-hook` console script next to this interpreter: it skips
    `python -m`'s runpy machinery (~10ms per call). Fall back to `-m` otherwise."""
    script = Path(sys.executable).parent / HOOK_SCRIPT
    if script.exists():
        return f"{shlex.quote(str(script))} {name}"
    return f"{shlex.quote(sys.executable)} -m {MARKER} {name}"


def _owned(cmd: Any) -> bool:
    return isinstance(cmd, str) and (MARKER in cmd or f"/{HOOK_SCRIPT} " in cmd)


def load_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text().strip()
    if not text:
        return {}
    data = json.loads(text)  # let a JSONDecodeError surface: never clobber a broken file
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    dest = path.with_name(f"{path.name}.claudepilot-backup-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, dest)
    return dest


def apply_install(settings: dict[str, Any], hooks: bool = True) -> tuple[dict[str, Any], str | None]:
    """Pure transform. Returns (new_settings, previous_user_statusline_command).

    hooks=False installs only the statusline (no router, token mode or guard).
    """
    settings = json.loads(json.dumps(settings))  # deep copy
    chained = None
    current = settings.get("statusLine")
    if isinstance(current, dict) and current.get("command") and not _owned(current["command"]):
        chained = current["command"]
    settings["statusLine"] = {"type": "command", "command": hook_command("statusline"),
                              "padding": 0}

    if not hooks:
        return settings, chained
    registered = settings.setdefault("hooks", {})
    for event, name in HOOK_EVENTS.items():
        entries = [e for e in registered.get(event, []) if not _is_owned_entry(e)]
        entry: dict[str, Any] = {"hooks": [{"type": "command", "command": hook_command(name),
                                            "timeout": 10}]}
        if event in MATCHERS:
            entry = {"matcher": MATCHERS[event], **entry}
        entries.append(entry)
        registered[event] = entries
    return settings, chained


def apply_uninstall(settings: dict[str, Any], chained: str | None) -> dict[str, Any]:
    settings = json.loads(json.dumps(settings))
    current = settings.get("statusLine")
    if isinstance(current, dict) and _owned(current.get("command")):
        if chained:
            settings["statusLine"] = {"type": "command", "command": chained}
        else:
            settings.pop("statusLine")
    hooks = settings.get("hooks") or {}
    for event in list(hooks):
        kept = [e for e in hooks[event] if not _is_owned_entry(e)]
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event)
    if "hooks" in settings and not settings["hooks"]:
        settings.pop("hooks")
    return settings


def _is_owned_entry(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    handlers = entry.get("hooks") or []
    return bool(handlers) and all(_owned(h.get("command")) for h in handlers if isinstance(h, dict))


def is_installed(settings: dict[str, Any], hooks_expected: bool = True) -> dict[str, bool]:
    hooks = settings.get("hooks") or {}
    out = {"statusLine": _owned((settings.get("statusLine") or {}).get("command"))}
    for event in HOOK_EVENTS if hooks_expected else ():
        out[event] = any(_is_owned_entry(e) for e in hooks.get(event, []))
    return out


def _write(path: Path, settings: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.claudepilot-tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n")
    tmp.replace(path)


def install(path: Path | None = None, hooks: bool = True) -> tuple[Path, Path | None, str | None]:
    path = path or claude_settings_path()
    settings = load_settings(path)
    bak = backup(path)
    new, chained = apply_install(apply_uninstall(settings, None) if not hooks else settings, hooks=hooks)
    meta_path = state.state_dir() / "install.json"
    meta = state.read_json(meta_path)
    if chained:
        meta["chained_statusline"] = chained
    meta["installed_at"] = time.time()
    meta["hooks"] = hooks
    state.write_json(meta_path, meta)
    _write(path, new)
    return path, bak, chained or meta.get("chained_statusline")


def uninstall(path: Path | None = None) -> tuple[Path, Path | None]:
    path = path or claude_settings_path()
    settings = load_settings(path)
    bak = backup(path)
    meta_path = state.state_dir() / "install.json"
    meta = state.read_json(meta_path)
    _write(path, apply_uninstall(settings, meta.get("chained_statusline")))
    meta_path.unlink(missing_ok=True)
    return path, bak


def agents_dir() -> Path:
    return claude_settings_path().parent / "agents"


def _agent_source(name: str) -> str:
    from claudepilot.paths import data_file

    return data_file("agents", f"{name}.md")


def install_agents(target: Path | None = None) -> list[str]:
    """Copy the delegate subagents. Never overwrites a same-named agent the user wrote."""
    target = target or agents_dir()
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for name in AGENT_NAMES:
        dest = target / f"{name}.md"
        if dest.exists() and AGENT_MARKER not in dest.read_text():
            continue
        dest.write_text(_agent_source(name))
        written.append(name)
    return written


def uninstall_agents(target: Path | None = None) -> list[str]:
    target = target or agents_dir()
    removed = []
    for name in AGENT_NAMES:
        dest = target / f"{name}.md"
        if dest.exists() and AGENT_MARKER in dest.read_text():
            dest.unlink()
            removed.append(name)
    return removed


def agents_available() -> bool:
    """True when the delegate agents are installed (as files or through the plugin)."""
    import os

    if os.environ.get("CLAUDE_PLUGIN_ROOT"):
        return True
    return all((agents_dir() / f"{n}.md").exists() for n in AGENT_NAMES)


def plugin_enabled(settings: dict[str, Any]) -> bool:
    """True when the ClaudePilot Claude Code plugin is enabled (it brings its own hooks)."""
    enabled = settings.get("enabledPlugins") or {}
    return any(k.split("@", 1)[0] == "claudepilot" and v for k, v in enabled.items())
