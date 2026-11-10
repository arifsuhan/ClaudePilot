"""Conversation-aware routing: adjust a single-prompt decision using this session's history.

  follow-up   "yes", "ok go ahead", "continue", or a short prompt with no rule signal right
              after recent activity. Stay on the current model, because switching mid-task
              to Haiku on a "yes" would hand a complex task to the weakest model.
  escalate    "still failing", "same error", "doesn't work". Go one tier above both the
              recommendation and what was just used, capped at Opus.

History comes from route events (no prompt text) for the same pane.
"""

from __future__ import annotations

import re
from typing import Any

from claudepilot.router.classifier import TIER_ORDER, Decision, RuleSet, default_rules

TIERS = sorted(TIER_ORDER, key=TIER_ORDER.get)
RECENT_SECONDS = 15 * 60

ACK = re.compile(
    r"^\s*(y|ya|yes|yeah|yep|yup|ok|okay|k|sure|go|go ahead|go on|do it|continue|proceed|next|"
    r"carry on|keep going|thanks|thank you|thx|ty|lgtm|sounds good|perfect|great|nice|cool|"
    r"looks good|ship it|approved?|agreed?)([\s,.!]+(please|thanks|thank you|go ahead|do it))*[\s.!]*$",
    re.IGNORECASE,
)
FAILURE = re.compile(
    r"\b(still (fail\w*|broken|wrong|erroring|crash\w*|not working|doesn'?t work|the same)|"
    r"(does|did|is)n'?t work(ing)?|not (fixed|working)|same (error|issue|problem|bug)|"
    r"(that|this|it)('?s| is) (wrong|incorrect|broken)|try again|another error|new error|"
    r"made it worse|still getting)\b",
    re.IGNORECASE,
)


def _recent_routes(events: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
    return [e for e in events if e.get("kind") == "route" and now - float(e.get("ts") or 0) <= RECENT_SECONDS]


def _with(decision: Decision, rules: RuleSet, tier: str, reason: str, rule: str) -> Decision:
    info = rules.tiers.get(tier, {})
    return decision._replace(tier=tier, alias=info.get("alias", tier),
                             label=info.get("label", tier.title()), reason=reason, rule=rule)


def adjust(decision: Decision, prompt: str, current_tier: str | None,
           events: list[dict[str, Any]], now: float, rules: RuleSet | None = None) -> Decision:
    rules = rules or default_rules()
    recent = _recent_routes(events, now)
    last = recent[-1] if recent else None

    if FAILURE.search(prompt):
        floor = max(
            (TIER_ORDER[t] for t in (decision.tier, current_tier, (last or {}).get("recommended"))
             if t in TIER_ORDER),
            default=TIER_ORDER["sonnet"],
        )
        tier = TIERS[min(floor + 1, len(TIERS) - 1)]
        return _with(decision, rules, tier, "repeated failure, escalating", "context-escalate")

    stay_on = current_tier or (last or {}).get("current")
    if stay_on in TIER_ORDER and TIER_ORDER[decision.tier] < TIER_ORDER[stay_on]:
        if ACK.match(prompt):
            return _with(decision, rules, stay_on, "follow-up, staying on current model", "context-followup")
        # A short prompt with no rule signal, right after recent work, is almost always a
        # continuation ("now the same for the API layer").
        if decision.rule == "length" and last is not None:
            return _with(decision, rules, stay_on, "short follow-up, staying on current model",
                         "context-followup")
    return decision
