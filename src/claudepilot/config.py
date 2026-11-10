"""User configuration (~/.config/claudepilot/config.toml) with safe defaults."""

from __future__ import annotations

import copy
import sys
from typing import Any

from claudepilot.paths import config_dir

DEFAULTS: dict[str, Any] = {
    "router": {
        # off | advise | auto. auto is experimental and needs the tmux cockpit;
        # outside tmux it falls back to advise.
        "mode": "auto",
        # Skip switch advice when the conversation is at least this many tokens:
        # switching models forfeits the prompt cache and re-sends the whole context.
        "switch_cost_tokens": 150_000,
        # With a large context, ask Claude to use the claudepilot-quick/-deep subagents
        # instead of switching the session's model.
        "delegate": True,
    },
    "tokens": {
        # off | auto. auto adds a brevity instruction to each prompt: "lite" normally,
        # "caveman" (ultra-terse) when usage or context pressure is high.
        "mode": "auto",
        "caveman_usage": 75,    # % of the 5h or weekly window
        "caveman_context": 70,  # % of the context window
    },
    "guard": {
        # off | ask | deny: what happens when Claude touches a secret-looking file
        "mode": "ask",
    },
    "statusline": {
        # compact: one line. full: adds usage bars, forecast and modes (for use without tmux).
        "style": "compact",
    },
    "files": {
        # Editor for Enter in the Files panel. Falls back to $VISUAL, $EDITOR, vim, vi, nano.
        "editor": "vim",
    },
    "guide": {
        "tips": 3,              # tips shown at once in the sidebar
        "rotate_seconds": 30,
    },
    "alerts": {
        "thresholds": [75, 90],
        "desktop": True,
        "bell": True,
        "on_reset": True,
    },
    "workspace": {
        "sidebar_width": 42,
        "claude_command": "claude",
        # End the whole tmux session (sidebar included) when Claude Code exits.
        "close_with_claude": True,
        # Ctrl+arrows / Alt+arrows switch panes without the tmux prefix (ClaudePilot sessions only).
        "nav_keys": True,
        # Click a panel to focus it. Hold Shift while dragging to select text normally.
        "mouse": True,
        "refresh_seconds": 1.0,
    },
}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load() -> dict[str, Any]:
    path = config_dir() / "config.toml"
    if not path.exists():
        return copy.deepcopy(DEFAULTS)
    # Imported only when a config file exists (~9ms, paid by every hook call otherwise).
    if sys.version_info >= (3, 11):
        import tomllib
    else:  # pragma: no cover
        import tomli as tomllib
    try:
        with path.open("rb") as fh:
            return _merge(DEFAULTS, tomllib.load(fh))
    except (OSError, tomllib.TOMLDecodeError):
        return copy.deepcopy(DEFAULTS)


MODES = {
    "router": ({"off", "advise", "auto"}, "auto"),
    "tokens": ({"off", "auto"}, "auto"),
    "guard": ({"off", "ask", "deny"}, "ask"),
}


def mode(feature: str) -> str:
    """Runtime override (`claudepilot router|tokens ...`) wins over the config file."""
    from claudepilot import state

    allowed, default = MODES[feature]
    override = state.read_json(state.state_dir() / f"{feature}_mode.json").get("mode")
    value = override or load().get(feature, {}).get("mode")
    return value if value in allowed else default


def set_mode(feature: str, value: str) -> None:
    from claudepilot import state

    state.write_json(state.state_dir() / f"{feature}_mode.json", {"mode": value})


def router_mode() -> str:
    return mode("router")


def token_mode() -> str:
    return mode("tokens")


def guard_mode() -> str:
    return mode("guard")
