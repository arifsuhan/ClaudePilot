"""Filesystem locations. Everything ClaudePilot writes lives under these dirs."""

from __future__ import annotations

import os
import re
from pathlib import Path


def _xdg(var: str, default: str) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else Path.home() / default


def state_dir() -> Path:
    override = os.environ.get("CLAUDEPILOT_STATE_DIR")
    base = Path(override) if override else _xdg("XDG_STATE_HOME", ".local/state") / "claudepilot"
    base.mkdir(parents=True, exist_ok=True)
    return base


def config_dir() -> Path:
    override = os.environ.get("CLAUDEPILOT_CONFIG_DIR")
    return Path(override) if override else _xdg("XDG_CONFIG_HOME", ".config") / "claudepilot"


def claude_settings_path() -> Path:
    override = os.environ.get("CLAUDEPILOT_CLAUDE_SETTINGS")
    if override:
        return Path(override)
    claude_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(claude_dir) if claude_dir else Path.home() / ".claude") / "settings.json"


def data_file(*parts: str) -> str:
    """Text of a bundled file under claudepilot/data.

    Reads it next to this module (normal and editable installs) and only falls back to
    importlib.resources for zipped installs: importing that costs ~13ms (it pulls in
    inspect), which matters in hooks that run on every prompt or tool call.
    """
    path = Path(__file__).parent.joinpath("data", *parts)
    try:
        return path.read_text()
    except OSError:
        from importlib import resources

        return resources.files("claudepilot.data").joinpath(*parts).read_text()


def data_path(*parts: str) -> Path:
    return Path(__file__).parent.joinpath("data", *parts)


def safe_key(raw: str) -> str:
    """Turn a tmux pane id ('%3') or session id into a safe filename stem."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", raw) or "default"
