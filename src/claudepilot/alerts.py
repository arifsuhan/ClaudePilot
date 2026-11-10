"""Threshold and reset alerts (desktop notification; the sidebar rings the bell).

Fired from the statusline hook, which is the one place that sees every usage update.
Deduplicated per window per reset period, so each threshold fires at most once.
"""

from __future__ import annotations

from typing import Any

from claudepilot import state
from claudepilot.usage.limits import Window


def _notify(title: str, body: str, desktop: bool) -> None:
    if not desktop:
        return
    import platform  # lazy: the statusline hook runs often and rarely notifies
    import shutil
    import subprocess

    try:
        if platform.system() == "Darwin" and shutil.which("osascript"):
            script = f'display notification "{body}" with title "{title}"'
            subprocess.Popen(["osascript", "-e", script], stderr=subprocess.DEVNULL)
        elif shutil.which("notify-send"):
            subprocess.Popen(["notify-send", "-a", "ClaudePilot", title, body],
                             stderr=subprocess.DEVNULL)
    except OSError:
        pass


def check(windows: dict[str, Window], cfg: dict[str, Any]) -> list[str]:
    """Fire any newly crossed thresholds. Returns the messages fired (for tests)."""
    path = state.state_dir() / "alerts.json"
    seen = state.read_json(path)
    thresholds = sorted(int(t) for t in cfg.get("thresholds", [75, 90]))
    fired: list[str] = []

    for name, win in windows.items():
        period = win.resets_at
        entry = seen.get(name) or {}
        prev = entry.get("period")
        # Same window if resets_at moved by less than 10 minutes (tolerate jitter).
        if prev is None or period is None:
            same = bool(entry) and prev == period
        else:
            same = abs(float(prev) - period) < 600
        if not same:
            if prev is not None and cfg.get("on_reset", True):
                fired.append(f"{win.label} limit reset. Fresh window.")
            entry = {"period": period, "fired": []}
        for t in thresholds:
            if win.used_percentage >= t and t not in entry["fired"]:
                entry["fired"].append(t)
                fired.append(f"{win.label} usage at {win.used_percentage:.0f}% (≥{t}%)")
        seen[name] = entry

    if fired:
        state.write_json(path, seen)
        for msg in fired:
            _notify("ClaudePilot", msg, cfg.get("desktop", True))
        for msg in fired:
            state.append_event("alert", message=msg)
    elif windows:
        state.write_json(path, seen)
    return fired
