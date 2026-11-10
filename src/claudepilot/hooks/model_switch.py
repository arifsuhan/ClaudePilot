"""Model-switch hooks (Claude Code v2.1.251+).

PreModelSwitch: when the prompt cache is warm, Claude Code asks you to confirm a switch.
That dialog would stall auto mode, so we answer "allow", but only for the switch our
resubmit helper just started (marker file, matching tier, at most SWITCH_TTL old).
Every other switch goes through Claude Code's normal flow.

PostModelSwitch: record the confirmed change.
"""

from __future__ import annotations

import time
from typing import Any

from claudepilot import state
from claudepilot.router.classifier import tier_of_model

SWITCH_TTL = 30.0


def switching_path(key: str):
    return state.state_dir() / "panes" / f"{key}.switching.json"


def pre_run(payload: dict[str, Any]) -> dict[str, Any] | None:
    key = state.pane_key(payload.get("session_id"))
    marker = state.read_json(switching_path(key))
    if not marker or time.time() - float(marker.get("ts", 0)) > SWITCH_TTL:
        return None
    if tier_of_model(payload.get("to_model")) != marker.get("tier"):
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreModelSwitch",
            "permissionDecision": "allow",
            "permissionDecisionReason": "ClaudePilot auto-routing",
        }
    }


def run(payload: dict[str, Any]) -> None:
    key = state.pane_key(payload.get("session_id"))
    switching_path(key).unlink(missing_ok=True)
    to_model = payload.get("to_model")
    state.append_event(
        "model_switch",
        pane=key,
        from_model=payload.get("from_model"),
        to_model=to_model,
        source=payload.get("source"),
    )
    # The switch has already happened, so update the pane's model now instead of
    # waiting for the next statusline refresh. This is confirmed state, not intended state.
    record = state.read_json(state.state_dir() / "panes" / f"{key}.json")
    if record and to_model:
        model = record.setdefault("payload", {}).setdefault("model", {})
        if model.get("id") != to_model:
            model["id"] = to_model
            model["display_name"] = _display_name(to_model)
            state.write_json(state.state_dir() / "panes" / f"{key}.json", record)


def _display_name(model_id: str) -> str:
    for name in ("opus", "sonnet", "haiku", "fable"):
        if name in model_id.lower():
            return name.title()
    return model_id
