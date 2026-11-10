"""Local state shared between hooks (writers) and the sidebar (reader).

Layout under state_dir():
    latest.json            last statusline payload from any session (limits are account-wide)
    panes/<pane>.json      last statusline payload per Claude pane (model/context are per session)
    events.jsonl           router + model-switch events (no prompt text, ever)
    usage_history.jsonl    (ts, five_hour %, seven_day %) snapshots for forecasting
    alerts.json            which alert thresholds already fired per window
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from claudepilot.paths import safe_key, state_dir

__all__ = [
    "append_event",
    "append_usage",
    "pane_key",
    "read_events",
    "read_json",
    "read_session_state",
    "read_limits",
    "read_usage_history",
    "state_dir",
    "write_json",
    "write_limits",
    "write_session_state",
]

MAX_EVENTS = 20_000  # roughly a week or more of heavy use, for `claudepilot wrapped`
MAX_HISTORY = 5000


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Atomic write so the sidebar never reads a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Unique per process and thread; `tempfile` would cost hooks ~5ms of imports.
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def pane_key(session_id: str | None = None) -> str:
    """Identify the Claude session: its tmux pane when inside tmux, else its session id."""
    pane = os.environ.get("TMUX_PANE")
    return safe_key(pane or session_id or "default")


def write_session_state(payload: dict[str, Any], key: str) -> None:
    record = {"received_at": time.time(), "payload": payload}
    base = state_dir()
    write_json(base / "panes" / f"{key}.json", record)
    write_json(base / "latest.json", record)


def read_session_state(key: str | None = None, fallback: bool = True) -> dict[str, Any]:
    """Return {'received_at', 'payload'} for a pane, optionally falling back to latest."""
    base = state_dir()
    if key:
        record = read_json(base / "panes" / f"{safe_key(key)}.json")
        if record or not fallback:
            return record
    return read_json(base / "latest.json")


def write_limits(rate_limits: dict[str, Any], now: float | None = None) -> None:
    """Cache the last reported account-wide limits (see usage.limits.effective_windows)."""
    write_json(state_dir() / "limits.json",
               {"received_at": now or time.time(), "rate_limits": rate_limits})


def read_limits() -> dict[str, Any]:
    return read_json(state_dir() / "limits.json")


def _append_line(path: Path, obj: dict[str, Any], cap: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(obj) + "\n")
    # Cheap rotation: trim when the file grows well past the cap.
    try:
        if path.stat().st_size > cap * 400:
            lines = path.read_text().splitlines()[-cap:]
            path.write_text("\n".join(lines) + "\n")
    except OSError:
        pass


_TAIL_CACHE: dict[tuple[str, int], tuple[tuple[int, int], list[dict[str, Any]]]] = {}


def _tail_text(path: Path, limit: int) -> list[str]:
    """Last `limit` lines, read backwards from the end in blocks (not the whole file)."""
    with path.open("rb") as fh:
        fh.seek(0, 2)
        pos = fh.tell()
        block, data = 64 * 1024, b""
        while pos > 0 and data.count(b"\n") <= limit:
            step = min(block, pos)
            pos -= step
            fh.seek(pos)
            data = fh.read(step) + data
    lines = data.decode(errors="ignore").splitlines()
    if pos > 0:
        lines = lines[1:]  # first line may be partial
    return lines[-limit:]


def _read_lines(path: Path, limit: int) -> list[dict[str, Any]]:
    """Parse the last `limit` JSON lines. Cached by (mtime, size): long-lived panels that
    poll every second re-parse only after the file actually changes."""
    try:
        st = path.stat()
    except OSError:
        return []
    sig = (st.st_mtime_ns, st.st_size)
    key = (str(path), limit)
    hit = _TAIL_CACHE.get(key)
    if hit and hit[0] == sig:
        return hit[1]
    try:
        lines = _tail_text(path, limit)
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    if len(_TAIL_CACHE) > 32:
        _TAIL_CACHE.clear()
    _TAIL_CACHE[key] = (sig, out)
    return out


def file_sig(*paths: Path) -> tuple[tuple[int, int], ...]:
    """Cheap change signature for state files (used by panels to skip idle redraws)."""
    out = []
    for p in paths:
        try:
            st = p.stat()
            out.append((st.st_mtime_ns, st.st_size))
        except OSError:
            out.append((0, 0))
    return tuple(out)


def append_event(kind: str, **fields: Any) -> None:
    _append_line(state_dir() / "events.jsonl", {"ts": time.time(), "kind": kind, **fields}, MAX_EVENTS)


def read_events(limit: int = 50, key: str | None = None) -> list[dict[str, Any]]:
    # Parse only the tail: the sidebar calls this every second. When filtering by pane,
    # read a wider tail so other sessions' events don't crowd this one out.
    events = _read_lines(state_dir() / "events.jsonl", min(MAX_EVENTS, limit * (8 if key else 1)))
    if key:
        events = [e for e in events if e.get("pane") in (None, safe_key(key))]
    return events[-limit:]


def append_usage(five_hour: float | None, seven_day: float | None, ts: float | None = None) -> None:
    if five_hour is None and seven_day is None:
        return
    path = state_dir() / "usage_history.jsonl"
    last = _read_lines(path, 1)
    snap = {"ts": ts or time.time(), "five_hour": five_hour, "seven_day": seven_day}
    if last and last[0].get("five_hour") == five_hour and last[0].get("seven_day") == seven_day:
        return  # unchanged: keep the history small
    _append_line(path, snap, MAX_HISTORY)


def read_usage_history(limit: int = MAX_HISTORY) -> list[dict[str, Any]]:
    return _read_lines(state_dir() / "usage_history.jsonl", limit)
