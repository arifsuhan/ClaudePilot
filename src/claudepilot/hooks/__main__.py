"""Lightweight hook dispatcher: `python -m claudepilot.hooks <name>`.

Kept separate from the Typer CLI so hooks start fast (no rich/typer import).
"""

from __future__ import annotations

import json
import os
import sys


def _read_stdin() -> dict:
    try:
        data = json.load(sys.stdin)
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m claudepilot.hooks "
              "<statusline|prompt|guard|pre-model-switch|model-switch|resubmit>", file=sys.stderr)
        return 0
    name, rest = argv[0], argv[1:]
    try:
        if name == "statusline":
            from claudepilot.hooks import statusline

            sys.stdout.write(statusline.run(_read_stdin()))
        elif name == "prompt":
            from claudepilot.hooks import prompt

            out = prompt.run(_read_stdin())
            if out:
                sys.stdout.write(json.dumps(out))
        elif name == "guard":
            from claudepilot.hooks import guard

            out = guard.run(_read_stdin())
            if out:
                sys.stdout.write(json.dumps(out))
        elif name == "pre-model-switch":
            from claudepilot.hooks import model_switch

            out = model_switch.pre_run(_read_stdin())
            if out:
                sys.stdout.write(json.dumps(out))
        elif name == "model-switch":
            from claudepilot.hooks import model_switch

            model_switch.run(_read_stdin())
        elif name == "sync":
            from claudepilot.storage import db

            db.sync()
        elif name == "resubmit":
            from claudepilot.hooks import resubmit

            resubmit.run(rest)
    except Exception:  # noqa: BLE001 - never break Claude Code
        import traceback  # only on errors: importing it costs ~8ms per hook call

        if os.environ.get("CLAUDEPILOT_DEBUG"):
            traceback.print_exc()
        try:
            from claudepilot import state

            state.append_event("hook_error", hook=name, error=traceback.format_exc(limit=2)[-300:])
        except Exception:  # noqa: BLE001
            pass
    return 0


def entry() -> None:
    """Console script `claudepilot-hook <name>` (used by the Claude Code plugin)."""
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    entry()
