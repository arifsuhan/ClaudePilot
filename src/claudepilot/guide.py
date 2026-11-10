"""Sidebar GUIDE: contextual warnings first, then rotating tips from tips.yaml.

Contextual tips react to live state (context fill, usage pressure, permission mode,
an API key in the environment, unignored .env files, Opus overuse). Static tips rotate
round-robin across categories, so security, policy, tokens and model guidance all appear.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import yaml

from claudepilot.paths import config_dir, data_file

CATEGORY_ORDER = ("security", "tokens", "policy", "model")
LABELS = {"security": "SEC", "policy": "POLICY", "tokens": "TOKENS", "model": "MODEL"}


@dataclass(frozen=True)
class Tip:
    category: str
    text: str
    urgent: bool = False

    @property
    def label(self) -> str:
        return LABELS.get(self.category, self.category.upper())


@lru_cache(maxsize=1)
def static_tips() -> tuple[Tip, ...]:
    user_file = config_dir() / "tips.yaml"
    try:
        if user_file.exists():
            raw = yaml.safe_load(user_file.read_text()) or {}
        else:
            raw = yaml.safe_load(data_file("tips.yaml")) or {}
    except (OSError, yaml.YAMLError):
        raw = {}
    by_cat = {cat: [str(t) for t in (raw.get(cat) or [])] for cat in raw}
    cats = [c for c in CATEGORY_ORDER if c in by_cat] + [c for c in by_cat if c not in CATEGORY_ORDER]
    # Interleave the categories so rotation alternates between them.
    out: list[Tip] = []
    for i in range(max((len(v) for v in by_cat.values()), default=0)):
        for cat in cats:
            if i < len(by_cat[cat]):
                out.append(Tip(cat, by_cat[cat][i]))
    return tuple(out)


def contextual_tips(
    *,
    ctx_pct: float | None,
    usage_pct: float,
    model_tier: str | None,
    events: list[dict[str, Any]],
    permission_mode: str | None,
    unignored_secrets: list[str],
    env: dict[str, str] | None = None,
) -> list[Tip]:
    env = os.environ if env is None else env
    tips: list[Tip] = []
    if permission_mode == "bypassPermissions":
        tips.append(Tip("security", "Permissions are BYPASSED: Claude runs tools without asking. "
                                    "Use this only in a sandbox.", urgent=True))
    if unignored_secrets:
        names = ", ".join(unignored_secrets[:2])
        tips.append(Tip("security", f"{names} not gitignored. Add it to .gitignore and deny "
                                    "Read(./.env) in settings.", urgent=True))
    if env.get("ANTHROPIC_API_KEY"):
        tips.append(Tip("policy", "ANTHROPIC_API_KEY is set: Claude Code may bill the API instead of "
                                  "your subscription.", urgent=True))
    if ctx_pct is not None and ctx_pct >= 80:
        tips.append(Tip("tokens", f"Context {ctx_pct:.0f}% full: /compact now, or /clear for a new task.",
                        urgent=True))
    elif ctx_pct is not None and ctx_pct >= 60:
        tips.append(Tip("tokens", f"Context {ctx_pct:.0f}%: /context shows what is filling it."))
    if usage_pct >= 90:
        tips.append(Tip("tokens", f"Usage {usage_pct:.0f}%: keep prompts caveman-short and use Haiku "
                                  "for small asks.", urgent=True))
    routes = [e for e in events if e.get("kind") == "route"][-5:]
    simple = [e for e in routes if e.get("recommended") == "haiku" and e.get("current") == "opus"]
    if model_tier == "opus" and len(simple) >= 3:
        if sum(1 for e in simple if e.get("action") == "stay") * 2 > len(simple):
            # The router kept Opus to protect a large cached context, so /model would cost more.
            tips.append(Tip("model", f"{len(simple)} of your last {len(routes)} prompts were simple, but the "
                                     "context is large. /clear between tasks lets Haiku take them."))
        else:
            tips.append(Tip("model", f"{len(simple)} of your last {len(routes)} prompts were simple. "
                                     "/model haiku saves usage."))
    return tips


def pick(contextual: list[Tip], now: float, count: int, rotate_seconds: float) -> list[Tip]:
    """Contextual tips first (urgent first), then fill with rotating static tips."""
    chosen = sorted(contextual, key=lambda t: not t.urgent)[:count]
    pool = static_tips()
    if pool and len(chosen) < count:
        start = int(now // max(rotate_seconds, 1)) * count
        for i in range(len(pool)):
            if len(chosen) >= count:
                break
            tip = pool[(start + i) % len(pool)]
            if tip not in chosen:
                chosen.append(tip)
    return chosen
