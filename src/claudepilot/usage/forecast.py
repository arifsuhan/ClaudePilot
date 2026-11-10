"""Burn-rate forecast: "at this pace you hit your 5h limit at 15:40".

This is an ESTIMATE from recent reported-usage snapshots (linear fit over a
lookback window). Always label it as such in the UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MIN_SPAN_SECONDS = 15 * 60  # shorter spans give jumpy forecasts from a single burst
LOOKBACK_SECONDS = {"five_hour": 60 * 60, "seven_day": 24 * 60 * 60}


@dataclass(frozen=True)
class Forecast:
    window: str
    rate_per_hour: float  # percentage points per hour
    hits_at: float | None  # epoch seconds; None when the window resets first
    resets_first: bool


def _current_segment(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Keep only points after the most recent reset (a drop in usage)."""
    start = 0
    for i in range(1, len(points)):
        if points[i][1] + 0.5 < points[i - 1][1]:
            start = i
    return points[start:]


def forecast(
    history: list[dict[str, Any]],
    window: str,
    current_pct: float,
    resets_at: float | None,
    now: float,
) -> Forecast | None:
    lookback = LOOKBACK_SECONDS.get(window, 3600)
    points = [
        (float(h["ts"]), float(h[window]))
        for h in history
        if h.get(window) is not None and now - float(h["ts"]) <= lookback
    ]
    points.append((now, current_pct))
    points.sort()
    points = _current_segment(points)
    if len(points) < 2:
        return None
    (t0, p0), (t1, p1) = points[0], points[-1]
    span = t1 - t0
    if span < MIN_SPAN_SECONDS or p1 <= p0:
        return None
    rate = (p1 - p0) / span  # pct per second
    remaining = max(0.0, 100.0 - current_pct)
    hits_at = now + remaining / rate
    resets_first = resets_at is not None and hits_at >= resets_at
    return Forecast(
        window=window,
        rate_per_hour=rate * 3600,
        hits_at=None if resets_first else hits_at,
        resets_first=resets_first,
    )
