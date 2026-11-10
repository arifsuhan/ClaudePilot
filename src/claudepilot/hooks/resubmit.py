"""Detached helper for experimental auto mode. See hooks/prompt.py for the flow."""

from __future__ import annotations

import argparse
import json
import subprocess
import time

from claudepilot import install, state
from claudepilot.hooks import model_switch
from claudepilot.paths import claude_settings_path
from claudepilot.router.classifier import tier_of_model

CONFIRM_TIMEOUT = 15.0


def _tmux(*args: str) -> None:
    subprocess.run(["tmux", *args], check=False, capture_output=True, timeout=5)


def _confirmed(key: str, tier: str, since: float) -> bool:
    for ev in state.read_events(limit=20, key=key):
        if ev.get("kind") == "model_switch" and ev.get("ts", 0) >= since \
                and tier_of_model(ev.get("to_model")) == tier:
            return True
    record = state.read_session_state(key, fallback=False)
    model = (record.get("payload") or {}).get("model") or {}
    return record.get("received_at", 0) >= since and (
        tier_of_model(model.get("id")) == tier or tier_of_model(model.get("display_name")) == tier
    )


_MISSING = object()


def _saved_model() -> object:
    """The user's saved default model (settings.json "model"), or _MISSING."""
    try:
        return install.load_settings(claude_settings_path()).get("model", _MISSING)
    except (OSError, ValueError):
        return _MISSING


def restore_saved_model(previous: object, alias: str) -> bool:
    """`/model` also saves the choice as the default for future sessions. Undo that for our
    switch, but only if the saved value is still the alias we set (the user didn't change it)."""
    path = claude_settings_path()
    try:
        settings = install.load_settings(path)
    except (OSError, ValueError):
        return False
    if settings.get("model") != alias or previous == alias:
        return False
    if previous is _MISSING:
        settings.pop("model", None)
    else:
        settings["model"] = previous
    tmp = path.with_suffix(".json.claudepilot-tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n")
    tmp.replace(path)
    return True


CLEAR_PRESSES = 40  # Ctrl-U deletes one input line per press in Claude Code


def _clear_input(pane: str) -> None:
    for _ in range(CLEAR_PRESSES):
        _tmux("send-keys", "-t", pane, "C-u")
    time.sleep(0.15)


def _buffer_text(buffer: str) -> str | None:
    try:
        proc = subprocess.run(["tmux", "show-buffer", "-b", buffer], capture_output=True,
                              text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def run(argv: list[str]) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pane", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--alias", required=True)
    parser.add_argument("--buffer", required=True)
    args = parser.parse_args(argv)

    text = _buffer_text(args.buffer)
    _tmux("delete-buffer", "-b", args.buffer)  # don't keep the prompt around in tmux
    time.sleep(0.6)  # let Claude Code finish handling the blocked prompt
    started = time.time()
    # Lets our PreModelSwitch hook approve this switch without the warm-cache dialog.
    state.write_json(model_switch.switching_path(args.key), {"tier": args.alias, "ts": started})
    previous_model = _saved_model()
    # Claude Code may put the blocked prompt back in the input box: clear it first, or
    # "/model" would be appended to the prompt instead of running as a command.
    _clear_input(args.pane)
    _tmux("send-keys", "-t", args.pane, "-l", f"/model {args.alias}")
    time.sleep(0.4)  # let the slash-command menu settle before Enter
    _tmux("send-keys", "-t", args.pane, "Enter")

    ok = False
    deadline = started + CONFIRM_TIMEOUT
    while time.time() < deadline:
        if _confirmed(args.key, args.alias, started):
            ok = True
            break
        time.sleep(0.3)
    model_switch.switching_path(args.key).unlink(missing_ok=True)
    if ok:
        # The running session keeps the new model; only the saved default is restored.
        restore_saved_model(previous_model, args.alias)

    # Type the prompt back literally. Pasting would wrap it in <pasted_content> and change it.
    _clear_input(args.pane)
    if text:
        _tmux("send-keys", "-t", args.pane, "-l", text.rstrip("\n"))
    if ok and text:
        time.sleep(0.3)
        _tmux("send-keys", "-t", args.pane, "Enter")
        state.append_event("auto_route", pane=args.key, to=args.alias, status="ok")
    else:
        state.append_event(
            "auto_route", pane=args.key, to=args.alias, status="unconfirmed",
            note="switch not confirmed; prompt restored but NOT sent",
        )
        _tmux("display-message", "-t", args.pane,
              f"ClaudePilot: could not confirm switch to {args.alias}. Prompt restored, not sent.")
