"""Token optimization: inject a brevity instruction into each prompt (off | auto).

Mechanism: the UserPromptSubmit hook returns `additionalContext`, which Claude Code
adds to the request. Output tokens are the expensive part of a turn, and terser
answers also keep the context window (and everything re-sent with it) smaller.

auto picks a level for each prompt:
  lite     the default: concise answers, no preamble or recap
  caveman  ultra-terse, used when usage or context pressure is high
  (none)   the prompt explicitly asks for detail, so leave it alone

Code, commands, paths, numbers and error text are never shortened.
"""

from __future__ import annotations

import re
from typing import Any

from claudepilot.usage.limits import effective_windows

LITE = (
    "[ClaudePilot token mode: lite] Answer concisely. Skip preamble, restating the request, "
    "and closing summaries. Keep code, commands, file paths and error messages complete and exact."
)
CAVEMAN = (
    "[ClaudePilot token mode: caveman. Usage is high] Reply ultra-terse, like a caveman: short "
    "fragments, no filler, no pleasantries, no recap. Drop articles when the meaning stays clear. "
    "Never shorten code, commands, file paths, numbers or error text. Give full detail only if "
    "the user asks for it."
)
INSTRUCTIONS = {"lite": LITE, "caveman": CAVEMAN}

WANTS_DETAIL = re.compile(
    r"\b(in detail|detailed|step[- ]by[- ]step|thorough(ly)?|verbose|explain (fully|everything)|"
    r"long[- ]form|write (a|the) (doc|readme|guide|article|blog|tutorial))\b",
    re.IGNORECASE,
)


def pressure(session: dict[str, Any], cache: dict[str, Any], now: float) -> tuple[float, float]:
    """(highest usage % across windows, context %), from the data we have."""
    wins = effective_windows(session, cache, now)
    usage = max((w.used_percentage for w in wins.values() if not w.reset_since), default=0.0)
    ctx = (session.get("context_window") or {}).get("used_percentage") or 0
    return usage, float(ctx)


def choose_level(prompt: str, usage_pct: float, ctx_pct: float, cfg: dict[str, Any]) -> str | None:
    if WANTS_DETAIL.search(prompt):
        return None
    if usage_pct >= float(cfg.get("caveman_usage", 75)) or ctx_pct >= float(cfg.get("caveman_context", 70)):
        return "caveman"
    return "lite"
