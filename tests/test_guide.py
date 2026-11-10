import subprocess

from claudepilot import guide, tokens
from claudepilot.explorer.git import unignored_secrets


def _ctx(**kw):
    base = dict(ctx_pct=10.0, usage_pct=10.0, model_tier="sonnet", events=[],
                permission_mode="default", unignored_secrets=[], env={})
    base.update(kw)
    return guide.contextual_tips(**base)


def test_quiet_state_has_no_contextual_tips():
    assert _ctx() == []


def test_contextual_warnings():
    texts = " ".join(t.text for t in _ctx(
        ctx_pct=85, usage_pct=95, permission_mode="bypassPermissions",
        unignored_secrets=[".env"], env={"ANTHROPIC_API_KEY": "x"}))
    for needle in ("/compact", "BYPASSED", ".env not gitignored", "bill the API", "caveman"):
        assert needle in texts


def test_opus_overuse_tip():
    events = [{"kind": "route", "recommended": "haiku", "current": "opus", "action": "switch"}] * 4
    [tip] = [t for t in _ctx(model_tier="opus", events=events) if t.category == "model"]
    assert "/model haiku" in tip.text


def test_opus_overuse_with_large_context_suggests_clear_not_switch():
    events = [{"kind": "route", "recommended": "haiku", "current": "opus", "action": "stay"}] * 4
    [tip] = [t for t in _ctx(model_tier="opus", events=events) if t.category == "model"]
    assert "/clear" in tip.text and "/model" not in tip.text


def test_pick_puts_urgent_first_and_fills_with_static():
    tips = guide.pick([guide.Tip("tokens", "a"), guide.Tip("security", "b", urgent=True)],
                      now=0, count=3, rotate_seconds=30)
    assert [t.text for t in tips[:2]] == ["b", "a"] and len(tips) == 3


def test_static_tips_rotate_across_categories():
    pool = guide.static_tips()
    assert {t.category for t in pool[:4]} == {"security", "tokens", "policy", "model"}
    assert guide.pick([], 0, 3, 30) != guide.pick([], 30, 3, 30)


def test_choose_level():
    cfg = {"caveman_usage": 75, "caveman_context": 70}
    assert tokens.choose_level("fix bug", 10, 10, cfg) == "lite"
    assert tokens.choose_level("fix bug", 80, 10, cfg) == "caveman"
    assert tokens.choose_level("fix bug", 10, 75, cfg) == "caveman"
    assert tokens.choose_level("write a step-by-step guide", 90, 90, cfg) is None


def test_unignored_secrets(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".env").write_text("X=1")
    (tmp_path / ".env.example").write_text("X=")
    (tmp_path / "server.pem").write_text("k")
    assert unignored_secrets(tmp_path) == [".env", "server.pem"]
    (tmp_path / ".gitignore").write_text(".env\n*.pem\n")
    assert unignored_secrets(tmp_path) == []


def test_unignored_secrets_outside_git(tmp_path):
    (tmp_path / ".env").write_text("X=1")
    assert unignored_secrets(tmp_path) == []


def test_bundled_tips_yaml_parses_and_is_complete():
    from importlib import resources

    import yaml

    raw = yaml.safe_load(resources.files("claudepilot.data").joinpath("tips.yaml").read_text())
    assert set(raw) == {"security", "policy", "tokens", "model"}
    for tips in raw.values():
        assert tips and all(isinstance(t, str) and len(t) <= 110 for t in tips)
