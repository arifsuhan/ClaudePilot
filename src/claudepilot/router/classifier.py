"""Rule-based prompt classifier. Fast (<1 ms), local, and deterministic.

The prompt text is used only inside `classify()` and never stored or logged.
"""

from __future__ import annotations

import contextlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, NamedTuple

from claudepilot.paths import config_dir, data_file, data_path

TIER_ORDER = {"haiku": 0, "sonnet": 1, "opus": 2}


# NamedTuples rather than dataclasses: this module runs on every prompt, and importing
# dataclasses (which pulls in inspect) costs ~12ms there.
class Decision(NamedTuple):
    tier: str
    alias: str
    label: str
    reason: str
    rule: str
    word_count: int


class Rule(NamedTuple):
    name: str
    tier: str
    weight: int
    reason: str
    patterns: list[re.Pattern[str]]


class RuleSet(NamedTuple):
    tiers: dict[str, dict[str, Any]]
    default_tier: str
    short_words: int
    long_words: int
    rules: list[Rule]


def _rules_source() -> tuple[str, str]:
    """(cache key, YAML text loader id): the user override if present, else the bundled file."""
    user_file = config_dir() / "models.yaml"
    if user_file.exists():
        st = user_file.stat()
        return f"user:{user_file}:{st.st_mtime_ns}:{st.st_size}", str(user_file)
    bundled = data_path("models.yaml")
    try:
        st = bundled.stat()
        return f"bundled:{bundled}:{st.st_mtime_ns}:{st.st_size}", ""
    except OSError:
        from claudepilot import __version__

        return f"bundled:{__version__}", ""


def _load_yaml() -> dict[str, Any]:
    """Parsed rules. PyYAML costs ~27ms to import, so the parsed result is cached as JSON
    and YAML is only loaded when the rules file changes."""
    key, user_path = _rules_source()
    cache = None
    try:
        from claudepilot.paths import state_dir

        cache = state_dir() / "rules-cache.json"
        cached = json.loads(cache.read_text())
        if cached.get("key") == key:
            return cached["rules"]
    except (OSError, ValueError, KeyError):
        pass
    import yaml

    text = (Path(user_path).read_text() if user_path
            else data_file("models.yaml"))
    raw = yaml.safe_load(text) or {}
    if cache is not None:
        with contextlib.suppress(OSError):
            cache.write_text(json.dumps({"key": key, "rules": raw}))
    return raw


def parse_rules(raw: dict[str, Any]) -> RuleSet:
    tiers = raw.get("tiers") or {}
    rules = []
    for item in raw.get("rules") or []:
        tier = item.get("tier")
        if tier not in tiers:
            continue
        compiled = [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in item.get("patterns", [])]
        rules.append(
            Rule(
                name=item.get("name", "unnamed"),
                tier=tier,
                weight=int(item.get("weight", 10)),
                reason=item.get("reason", item.get("name", "")),
                patterns=compiled,
            )
        )
    default = raw.get("default_tier", "sonnet")
    return RuleSet(
        tiers=tiers,
        default_tier=default if default in tiers else next(iter(tiers), "sonnet"),
        short_words=int(raw.get("short_prompt_words", 12)),
        long_words=int(raw.get("long_prompt_words", 250)),
        rules=rules,
    )


@lru_cache(maxsize=1)
def default_rules() -> RuleSet:
    return parse_rules(_load_yaml())


def _decision(rules: RuleSet, tier: str, reason: str, rule: str, words: int) -> Decision:
    info = rules.tiers.get(tier, {})
    return Decision(
        tier=tier,
        alias=info.get("alias", tier),
        label=info.get("label", tier.title()),
        reason=reason,
        rule=rule,
        word_count=words,
    )


def classify(prompt: str, rules: RuleSet | None = None) -> Decision:
    rules = rules or default_rules()
    text = prompt.strip()
    words = len(text.split())

    best: tuple[int, int, Rule] | None = None
    for rule in rules.rules:
        if any(p.search(text) for p in rule.patterns):
            key = (rule.weight, TIER_ORDER.get(rule.tier, 1))
            if best is None or key > best[:2]:
                best = (*key, rule)

    if best is not None:
        rule = best[2]
        tier = rule.tier
        # Very long prompts with a "lighter" match still deserve at least sonnet.
        if words > rules.long_words and TIER_ORDER.get(tier, 1) < TIER_ORDER["sonnet"]:
            return _decision(rules, "sonnet", f"{rule.reason} (long prompt)", rule.name, words)
        return _decision(rules, tier, rule.reason, rule.name, words)

    if words > rules.long_words:
        return _decision(rules, "opus", "long, detailed prompt", "length", words)
    if words <= rules.short_words:
        return _decision(rules, "haiku", "short prompt", "length", words)
    return _decision(rules, rules.default_tier, "general task", "default", words)


def tier_of_model(model_id: str | None) -> str | None:
    """Map a model id/display name ('claude-opus-5-5', 'Opus') to a tier."""
    if not model_id:
        return None
    lowered = model_id.lower()
    for tier in TIER_ORDER:
        if tier in lowered:
            return tier
    return None


def validate_file(path: Path) -> list[str]:
    """Return problems with a user models.yaml (used by `doctor`)."""
    import yaml

    problems = []
    try:
        raw = yaml.safe_load(path.read_text()) or {}
        rs = parse_rules(raw)
    except (OSError, yaml.YAMLError, re.error) as exc:
        return [f"{path}: {exc}"]
    if not rs.tiers:
        problems.append(f"{path}: no tiers defined")
    if not rs.rules:
        problems.append(f"{path}: no valid rules")
    return problems
