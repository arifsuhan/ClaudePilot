"""`claudepilot wrapped`: a shareable summary of the last N days, from local data only.

Sources: events.jsonl (router, token mode, guard, alerts) and usage_history.jsonl
(reported 5h and weekly usage). Contains no prompt text, file contents or paths, so
the card is safe to screenshot.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from claudepilot.router.classifier import TIER_ORDER

TIERS = ("haiku", "sonnet", "opus")


@dataclass
class Wrapped:
    since: float
    until: float
    prompts: int = 0
    model_mix: Counter = field(default_factory=Counter)  # tier the prompt ran on
    recommended_mix: Counter = field(default_factory=Counter)
    oversized: int = 0  # prompts run on a bigger model than the router recommended
    auto_switches: int = 0
    kept_for_cache: int = 0
    token_levels: Counter = field(default_factory=Counter)
    guard_hits: int = 0
    guard_files: Counter = field(default_factory=Counter)
    alerts: int = 0
    peak_five_hour: float | None = None
    peak_seven_day: float | None = None
    limit_hits: int = 0  # times the 5h window reached 100%
    near_misses: int = 0  # times it reached 90% but not 100%
    busiest_hour: int | None = None
    busiest_day: str | None = None


def compute(events: list[dict[str, Any]], history: list[dict[str, Any]],
            now: float, days: int = 7) -> Wrapped:
    since = now - days * 86_400
    w = Wrapped(since=since, until=now)
    hours: Counter = Counter()
    weekdays: Counter = Counter()

    for ev in events:
        ts = float(ev.get("ts") or 0)
        if ts < since or ts > now:
            continue
        kind = ev.get("kind")
        if kind == "route":
            w.prompts += 1
            cur, rec = ev.get("current"), ev.get("recommended")
            if cur in TIERS:
                w.model_mix[cur] += 1
            if rec in TIERS:
                w.recommended_mix[rec] += 1
            if cur in TIERS and rec in TIERS and TIER_ORDER[rec] < TIER_ORDER[cur]:
                w.oversized += 1
            if ev.get("action") == "stay":
                w.kept_for_cache += 1
            local = time.localtime(ts)
            hours[local.tm_hour] += 1
            weekdays[time.strftime("%A", local)] += 1
        elif kind == "auto_route" and ev.get("status") == "ok":
            w.auto_switches += 1
        elif kind == "tokens":
            w.token_levels[ev.get("level") or "skip"] += 1
        elif kind == "guard":
            w.guard_hits += 1
            if ev.get("file"):
                w.guard_files[ev["file"]] += 1
        elif kind == "alert":
            w.alerts += 1

    if hours:
        w.busiest_hour = hours.most_common(1)[0][0]
        w.busiest_day = weekdays.most_common(1)[0][0]

    in_range = sorted((h for h in history if since <= float(h.get("ts") or 0) <= now),
                      key=lambda h: h["ts"])
    prev = 0.0
    for snap in in_range:
        five, week = snap.get("five_hour"), snap.get("seven_day")
        if five is not None:
            w.peak_five_hour = max(w.peak_five_hour or 0.0, five)
            if five >= 100 > prev:
                w.limit_hits += 1
            elif five >= 90 > prev:
                w.near_misses += 1
            prev = five
        if week is not None:
            w.peak_seven_day = max(w.peak_seven_day or 0.0, week)
    return w


def _bar(n: int, total: int, width: int = 20) -> str:
    filled = round(n / total * width) if total else 0
    return "█" * filled + "░" * (width - filled)


def lines(w: Wrapped) -> list[tuple[str, str]]:
    """(text, rich style) pairs, shared by the Rich card and the --plain output."""
    span = (f"{time.strftime('%b %d', time.localtime(w.since))} - "
            f"{time.strftime('%b %d', time.localtime(w.until))}")
    out: list[tuple[str, str]] = [(f"Claude Code week: {span}", "bold"), ("", "")]
    if not w.prompts and w.peak_five_hour is None:
        out.append(("No data yet. Use Claude Code with ClaudePilot installed for a while.", "dim"))
        return out

    out.append((f"Prompts routed: {w.prompts}", "bold cyan"))
    total = sum(w.model_mix.values())
    for tier in TIERS:
        n = w.model_mix.get(tier, 0)
        if total:
            out.append((f"  {tier:<7}{_bar(n, total)} {n / total * 100:3.0f}%", ""))
    if w.oversized:
        pct = w.oversized / w.prompts * 100 if w.prompts else 0
        out.append((f"  {w.oversized} prompts ({pct:.0f}%) could have used a smaller model", "yellow"))
    if w.auto_switches or w.kept_for_cache:
        out.append((f"  auto-switched {_times(w.auto_switches)}, kept the model to protect the "
                    f"cache {_times(w.kept_for_cache)}", "dim"))
    out.append(("", ""))

    if w.peak_five_hour is not None:
        week = f"{w.peak_seven_day:.0f}%" if w.peak_seven_day is not None else "n/a"
        out.append((f"Peak usage: 5h {w.peak_five_hour:.0f}%   week {week}", "bold"))
        hits = f"Limit hits: {w.limit_hits}"
        if w.near_misses:
            hits += f"   close calls (90%+): {w.near_misses}"
        out.append((hits, "red" if w.limit_hits else "green"))
    if w.busiest_hour is not None:
        out.append((f"Busiest: {w.busiest_day}s around {w.busiest_hour:02d}:00", ""))
    if w.token_levels:
        lv = w.token_levels
        out.append((f"Token mode: lite {lv.get('lite', 0)}  caveman {lv.get('caveman', 0)}  "
                    f"skipped {lv.get('skip', 0)}", ""))
    if w.guard_hits:
        top = ", ".join(name for name, _ in w.guard_files.most_common(3))
        out.append((f"Secret guard stepped in {_times(w.guard_hits)}: {top}", "red"))
    out.append(("", ""))
    out.append((tip(w), "italic"))
    return out


def _times(n: int) -> str:
    return "once" if n == 1 else f"{n} times"


def tip(w: Wrapped) -> str:
    if w.limit_hits:
        return "Tip: you hit the 5h limit. Try caveman prompts and Haiku for small asks."
    if w.prompts and w.oversized / w.prompts >= 0.3:
        return "Tip: /clear between tasks so the router can move small asks to Haiku."
    if (w.peak_seven_day or 0) >= 80:
        return "Tip: weekly usage ran high. Plan heavy sessions right after a reset."
    return "Nice week: usage stayed comfortably within limits."
