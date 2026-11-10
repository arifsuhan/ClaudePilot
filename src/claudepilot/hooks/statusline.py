"""statusLine command: persist the payload for the sidebar, then print a status line.

If the user already had a statusline, `claudepilot install` saves it and we run it
here with the same stdin. Their status line keeps working, and we still get the data.

The printed line sticks to plain ASCII plus '%' so that it renders the same in every
terminal font (ambiguous-width glyphs like the circular arrow garbled the line in some terminals).
"""

from __future__ import annotations

import json
import subprocess
import time
from typing import Any

from claudepilot import alerts, config, state
from claudepilot.usage.limits import describe, effective_windows, windows


def chained_command() -> str | None:
    return state.read_json(state.state_dir() / "install.json").get("chained_statusline")


def _run_chained(cmd: str, payload: dict[str, Any]) -> str | None:
    try:
        proc = subprocess.run(
            cmd, shell=True, input=json.dumps(payload), capture_output=True, text=True, timeout=3
        )
        return proc.stdout
    except (OSError, subprocess.TimeoutExpired):
        return None


def render_line(payload: dict[str, Any], now: float, cache: dict[str, Any] | None = None) -> str:
    model = (payload.get("model") or {}).get("display_name") or "?"
    parts = [model]
    ctx = (payload.get("context_window") or {}).get("used_percentage")
    if ctx is not None:
        parts.append(f"ctx {float(ctx):.0f}%")
    wins = effective_windows(payload, cache or {}, now)
    parts.extend(describe(win, now) for win in wins.values())
    if not wins:
        parts.append("usage: after first reply")
    return " | ".join(parts)


def ascii_bar(pct: float, width: int = 10) -> str:
    filled = round(max(0.0, min(100.0, pct)) / 100 * width)
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def render_full(payload: dict[str, Any], now: float, cache: dict[str, Any],
                history: list[dict[str, Any]]) -> str:
    """Two lines for use without the tmux sidebar: summary, then bars, forecast and modes."""
    from claudepilot.usage.forecast import forecast

    second = []
    for name, win in effective_windows(payload, cache, now).items():
        if win.reset_since:
            second.append(f"{win.label} reset")
            continue
        text = f"{win.label} {ascii_bar(win.used_percentage)} {win.used_percentage:.0f}%"
        fc = forecast(history, name, win.used_percentage, win.resets_at, now) if win.live else None
        if fc and fc.hits_at:
            fmt = "%H:%M" if name == "five_hour" else "%a %H:%M"
            text += f" limit~{time.strftime(fmt, time.localtime(fc.hits_at))}(est)"
        second.append(text)
    second.append(f"router {config.router_mode()} . tokens {config.token_mode()} . "
                  f"guard {config.guard_mode()}")
    return render_line(payload, now, cache) + "\n" + " | ".join(second)


def run(payload: dict[str, Any]) -> str:
    now = time.time()
    if not payload:
        return "ClaudePilot: waiting for data"
    state.write_session_state(payload, state.pane_key(payload.get("session_id")))

    live = windows(payload, now)
    if live:
        state.write_limits(payload["rate_limits"], now)
        state.append_usage(
            live["five_hour"].used_percentage if "five_hour" in live else None,
            live["seven_day"].used_percentage if "seven_day" in live else None,
            ts=now,
        )
        alerts.check(live, config.load()["alerts"])

    chained = chained_command()
    if chained:
        out = _run_chained(chained, payload)
        if out is not None:
            return out
    if config.load()["statusline"]["style"] == "full":
        return render_full(payload, now, state.read_limits(), state.read_usage_history(500))
    return render_line(payload, now, state.read_limits())
