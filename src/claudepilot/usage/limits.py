"""Read the *reported* rate-limit windows out of a statusline payload.

Claude Code includes `rate_limits.five_hour` / `rate_limits.seven_day` for Pro / Max
subscribers, but only after a session's first response (or after `/usage`). Each window
can be absent.

Limits are account-wide, so the statusline hook also caches the last reported values
(`limits.json`). `effective_windows()` fills windows the current session hasn't reported
yet from that cache, so usage is visible as soon as Claude Code starts. Cached values are
marked with their age, and a cached window whose reset time has passed is shown as
freshly reset.
"""

from __future__ import annotations

from typing import Any, NamedTuple

WINDOWS = ("five_hour", "seven_day")
LABELS = {"five_hour": "5h", "seven_day": "week"}


class Window(NamedTuple):  # NamedTuple, not dataclass: hooks import this; dataclasses costs ~12ms
    name: str
    used_percentage: float
    resets_at: float | None
    # Seconds since the cached value was reported; None means live from this session.
    cached_age: float | None = None
    # True when the cached window's reset time has passed (usage restarted near 0%).
    reset_since: bool = False

    @property
    def label(self) -> str:
        return LABELS.get(self.name, self.name)

    @property
    def live(self) -> bool:
        return self.cached_age is None


def windows(payload: dict[str, Any], now: float | None = None) -> dict[str, Window]:
    """Windows reported live in this payload."""
    limits = payload.get("rate_limits") or {}
    out: dict[str, Window] = {}
    for name in WINDOWS:
        raw = limits.get(name)
        if not isinstance(raw, dict) or raw.get("used_percentage") is None:
            continue
        resets_at = raw.get("resets_at")
        if now is not None and resets_at is not None and resets_at <= now:
            continue  # stale: the window already reset
        out[name] = Window(name, float(raw["used_percentage"]), resets_at)
    return out


def effective_windows(
    payload: dict[str, Any], cache: dict[str, Any], now: float
) -> dict[str, Window]:
    """Live windows, with anything missing filled from the account-wide cache."""
    out = windows(payload, now)
    cached_limits = cache.get("rate_limits") or {}
    age = max(0.0, now - float(cache.get("received_at") or now))
    for name in WINDOWS:
        if name in out:
            continue
        raw = cached_limits.get(name)
        if not isinstance(raw, dict) or raw.get("used_percentage") is None:
            continue
        resets_at = raw.get("resets_at")
        if resets_at is not None and resets_at <= now:
            out[name] = Window(name, 0.0, None, cached_age=age, reset_since=True)
        else:
            out[name] = Window(name, float(raw["used_percentage"]), resets_at, cached_age=age)
    return {name: out[name] for name in WINDOWS if name in out}


def format_countdown(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86_400)
    hours, rem = divmod(rem, 3_600)
    minutes = rem // 60
    if days:
        return f"{days}d{hours:02d}h"
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m"


def describe(win: Window, now: float) -> str:
    """Compact, ASCII-safe text such as '5h 10% (3h38m)' or 'week 35% (10h48m, 4m ago)'."""
    if win.reset_since:
        return f"{win.label} reset"
    extras = []
    if win.resets_at:
        extras.append(format_countdown(win.resets_at - now))
    if win.cached_age is not None and win.cached_age >= 60:
        extras.append(f"{format_countdown(win.cached_age)} ago")
    text = f"{win.label} {win.used_percentage:.0f}%"
    return f"{text} ({', '.join(extras)})" if extras else text
