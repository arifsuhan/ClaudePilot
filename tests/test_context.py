import time

import pytest

from claudepilot.router import context
from claudepilot.router.classifier import classify

NOW = time.time()


def route(ago=60, recommended="opus", current="opus"):
    return {"kind": "route", "ts": NOW - ago, "recommended": recommended, "current": current}


@pytest.mark.parametrize("prompt", ["yes", "ok go ahead", "Continue.", "thanks!", "lgtm", "do it please"])
def test_ack_stays_on_current_model(prompt):
    d = context.adjust(classify(prompt), prompt, "opus", [route()], NOW)
    assert d.tier == "opus" and d.rule == "context-followup"


def test_ack_without_history_still_stays_when_model_known():
    d = context.adjust(classify("yes"), "yes", "opus", [], NOW)
    assert d.tier == "opus"


def test_short_followup_after_recent_work_stays():
    p = "now the same for billing"
    assert classify(p).rule == "length"
    assert context.adjust(classify(p), p, "opus", [route()], NOW).tier == "opus"


def test_short_prompt_without_recent_work_can_downgrade():
    p = "now the same for billing"
    assert context.adjust(classify(p), p, "opus", [route(ago=3600)], NOW).tier == "haiku"


def test_real_quick_question_still_downgrades():
    p = "what is a closure?"
    assert context.adjust(classify(p), p, "opus", [route()], NOW).tier == "haiku"


@pytest.mark.parametrize(("current", "expected"), [("haiku", "opus"), ("sonnet", "opus"), ("opus", "opus")])
def test_failure_escalates(current, expected):
    p = "still failing with the same error"
    d = context.adjust(classify(p), p, current, [route(recommended="sonnet", current=current)], NOW)
    assert d.tier == expected and d.rule == "context-escalate"


def test_failure_from_haiku_goes_one_tier_up():
    p = "that doesn't work"
    base = classify(p)
    d = context.adjust(base, p, "haiku", [route(recommended="haiku", current="haiku")], NOW)
    order = ["haiku", "sonnet", "opus"]
    floor = max(order.index(base.tier), order.index("haiku"))
    assert d.tier == order[min(floor + 1, 2)]
