import pytest

from claudepilot.router.classifier import classify, tier_of_model


@pytest.mark.parametrize(
    ("prompt", "tier"),
    [
        ("what is a closure?", "haiku"),
        ("fix the typo in README", "haiku"),
        ("summarize this file", "haiku"),
        ("Fix the failing test in auth middleware, it throws a KeyError", "sonnet"),
        ("implement a function that parses the config and add tests for it", "sonnet"),
        ("Design the architecture for a multi-tenant billing service with trade-offs", "opus"),
        ("there is a race condition in the worker pool, think hard about it", "opus"),
        ("refactor error handling across the entire codebase", "opus"),
    ],
)
def test_classify(prompt, tier):
    assert classify(prompt).tier == tier


def test_short_prompt_without_rule_is_haiku():
    assert classify("hello there").tier == "haiku"


def test_long_prompt_without_rule_is_opus():
    assert classify(" ".join(["lorem"] * 300)).tier == "opus"


def test_decision_has_alias_and_reason():
    d = classify("what is a monad?")
    assert d.alias == "haiku"
    assert d.reason


@pytest.mark.parametrize(
    ("model", "tier"),
    [("claude-opus-5-5", "opus"), ("Sonnet", "sonnet"), ("claude-haiku-5-5", "haiku"),
     (None, None), ("gpt-x", None)],
)
def test_tier_of_model(model, tier):
    assert tier_of_model(model) == tier


def test_rules_cache_refreshes_when_yaml_changes(isolated_dirs):
    from claudepilot.router import classifier

    user = isolated_dirs / "config" / "models.yaml"
    user.parent.mkdir(parents=True, exist_ok=True)
    user.write_text("tiers: {opus: {alias: opus}}\ndefault_tier: opus\nrules: []\n")
    assert classifier._load_yaml()["default_tier"] == "opus"
    assert (isolated_dirs / "state" / "rules-cache.json").exists()
    import os
    import time as _t

    user.write_text("tiers: {haiku: {alias: haiku}}\ndefault_tier: haiku\nrules: []\n")
    os.utime(user, (_t.time() + 5, _t.time() + 5))
    assert classifier._load_yaml()["default_tier"] == "haiku"
