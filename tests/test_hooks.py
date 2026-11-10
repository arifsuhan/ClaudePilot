import time

from conftest import payload

from claudepilot import alerts, state
from claudepilot.hooks import model_switch, prompt, statusline
from claudepilot.usage.limits import windows


def test_statusline_persists_and_renders():
    now = time.time()
    line = statusline.run(payload(five=40, week=10, resets5=now + 3600, resets7=now + 86400 * 3))
    assert "Opus" in line and "5h 40%" in line and "week 10%" in line
    rec = state.read_session_state("s1")
    assert rec["payload"]["model"]["id"] == "claude-opus-5-5"
    assert state.read_usage_history()[-1]["five_hour"] == 40


def test_statusline_handles_empty_input():
    assert "waiting" in statusline.run({})


def test_stale_window_is_dropped():
    now = time.time()
    assert windows(payload(five=99, resets5=now - 10), now) == {}


def test_prompt_never_stores_text():
    statusline.run(payload())
    secret = "what is the password hunter2?"
    prompt.run({"session_id": "s1", "prompt": secret})
    raw = (state.state_dir() / "events.jsonl").read_text()
    assert "hunter2" not in raw and "password" not in raw


def test_advise_mode_suggests_downgrade_on_opus():
    statusline.run(payload())
    out = prompt.run({"session_id": "s1", "prompt": "what is a closure?"})
    assert out and "Haiku" in out["systemMessage"]
    assert [e for e in state.read_events() if e["kind"] == "route"][-1]["action"] == "switch"


def test_no_advice_when_model_matches():
    statusline.run(payload(model="claude-haiku-5-5", name="Haiku"))
    assert "systemMessage" not in prompt.run({"session_id": "s1", "prompt": "what is a closure?"})


def test_stays_when_context_is_large():
    statusline.run(payload(ctx_tokens=150_000))
    assert "systemMessage" not in prompt.run({"session_id": "s1", "prompt": "what is a closure?"})
    assert [e for e in state.read_events() if e["kind"] == "route"][-1]["action"] == "stay"


def test_off_mode_and_slash_commands_skip():
    statusline.run(payload())
    assert prompt.run({"session_id": "s1", "prompt": "/compact"}) is None
    state.write_json(state.state_dir() / "router_mode.json", {"mode": "off"})
    assert "systemMessage" not in prompt.run({"session_id": "s1", "prompt": "what is a closure?"})
    state.write_json(state.state_dir() / "tokens_mode.json", {"mode": "off"})
    assert prompt.run({"session_id": "s1", "prompt": "what is a closure?"}) is None


def test_auto_mode_without_tmux_falls_back_to_advice():
    statusline.run(payload())
    state.write_json(state.state_dir() / "router_mode.json", {"mode": "auto"})
    out = prompt.run({"session_id": "s1", "prompt": "what is a closure?"})
    assert "systemMessage" in out and "decision" not in out


def test_auto_mode_loop_guard_passes_resubmitted_prompt():
    import hashlib

    statusline.run(payload())
    state.write_json(state.state_dir() / "router_mode.json", {"mode": "auto"})
    text = "what is a closure?"
    state.write_json(state.state_dir() / "panes" / "s1.pending.json",
                     {"sha": hashlib.sha256(text.encode()).hexdigest(), "ts": time.time()})
    out = prompt.run({"session_id": "s1", "prompt": text})
    assert "decision" not in out and "systemMessage" not in out  # passes through, tokens still apply
    assert "additionalContext" in out["hookSpecificOutput"]
    assert not (state.state_dir() / "panes" / "s1.pending.json").exists()


def test_model_switch_updates_confirmed_model():
    statusline.run(payload())
    model_switch.run({"session_id": "s1", "from_model": "claude-opus-5-5",
                      "to_model": "claude-haiku-5-5", "source": "command"})
    rec = state.read_session_state("s1")
    assert rec["payload"]["model"]["display_name"] == "Haiku"
    assert state.read_events()[-1]["kind"] == "model_switch"


def test_alerts_fire_once_per_threshold_and_on_reset(monkeypatch):
    monkeypatch.setattr(alerts, "_notify", lambda *a: None)
    cfg = {"thresholds": [75, 90], "on_reset": True}
    t = time.time() + 3600
    assert alerts.check(windows(payload(five=80, resets5=t), 0), cfg) == ["5h usage at 80% (≥75%)"]
    assert alerts.check(windows(payload(five=82, resets5=t + 5), 0), cfg) == []  # jitter tolerated
    assert len(alerts.check(windows(payload(five=95, resets5=t), 0), cfg)) == 1
    fired = alerts.check(windows(payload(five=2, resets5=t + 5 * 3600), 0), cfg)
    assert fired == ["5h limit reset. Fresh window."]


def test_new_session_shows_cached_limits_immediately():
    # Session A reports limits; session B has just started and has no rate_limits yet.
    now = time.time()
    statusline.run(payload(five=40, week=10, resets5=now + 3600, resets7=now + 86400))
    fresh = payload()
    fresh["session_id"] = "s2"
    line = statusline.run(fresh)
    assert "5h 40%" in line and "week 10%" in line


def test_cached_window_past_reset_shows_reset():
    now = time.time()
    state.write_limits({"five_hour": {"used_percentage": 90, "resets_at": now - 60}}, now - 3600)
    line = statusline.render_line(payload(), now, state.read_limits())
    assert "5h reset" in line and "90%" not in line


def test_statusline_is_ascii_safe():
    now = time.time()
    line = statusline.run(payload(five=40, week=10, resets5=now + 3600, resets7=now + 86400))
    assert all(ord(ch) < 128 for ch in line), line


def test_no_limits_anywhere_says_so():
    assert "after first reply" in statusline.run(payload())


def test_default_router_mode_is_auto():
    from claudepilot import config

    assert config.router_mode() == "auto"


def test_prompt_hook_ignores_other_sessions_model():
    statusline.run(payload())  # session s1 on Opus
    assert "systemMessage" not in prompt.run({"session_id": "brand-new", "prompt": "what is a closure?"})


def _ctx(out):
    return (out or {}).get("hookSpecificOutput", {}).get("additionalContext", "")


def test_token_mode_lite_by_default():
    statusline.run(payload(model="claude-haiku-5-5", name="Haiku"))
    assert "token mode: lite" in _ctx(prompt.run({"session_id": "s1", "prompt": "what is a closure?"}))


def test_token_mode_caveman_under_usage_pressure():
    now = time.time()
    statusline.run(payload(model="claude-haiku-5-5", name="Haiku", five=80, resets5=now + 3600))
    assert "caveman" in _ctx(prompt.run({"session_id": "s1", "prompt": "what is a closure?"}))


def test_token_mode_skips_prompts_asking_for_detail():
    statusline.run(payload(model="claude-haiku-5-5", name="Haiku"))
    assert _ctx(prompt.run({"session_id": "s1", "prompt": "explain closures in detail"})) == ""


def test_permission_mode_recorded_for_guide():
    statusline.run(payload())
    prompt.run({"session_id": "s1", "prompt": "hi", "permission_mode": "bypassPermissions"})
    meta = state.read_json(state.state_dir() / "panes" / "s1.meta.json")
    assert meta["permission_mode"] == "bypassPermissions"


def test_full_statusline_style_two_ascii_lines(isolated_dirs):
    cfg = isolated_dirs / "config"
    cfg.mkdir(exist_ok=True)
    (cfg / "config.toml").write_text('[statusline]\nstyle = "full"\n')
    now = time.time()
    out = statusline.run(payload(five=40, week=10, resets5=now + 3600, resets7=now + 86400))
    first, second = out.split("\n")
    assert "5h 40%" in first
    assert "[####------] 40%" in second and "router auto" in second and "guard ask" in second
    assert all(ord(ch) < 128 for ch in out)


def test_tail_read_and_cache(isolated_dirs):
    for i in range(3000):
        state.append_event("route", pane="p", n=i)
    assert [e["n"] for e in state.read_events(limit=3)] == [2997, 2998, 2999]
    first = state.read_events(limit=50)
    assert state.read_events(limit=50) == first  # served from the mtime cache
    state.append_event("route", pane="p", n=3000)
    assert state.read_events(limit=1)[0]["n"] == 3000  # cache invalidated by the write
