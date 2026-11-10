import json
import time
from pathlib import Path

from conftest import payload

from claudepilot import install, state
from claudepilot.hooks import model_switch, prompt, statusline
from claudepilot.storage import db

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "claudepilot"


def _route_events():
    return [e for e in state.read_events() if e["kind"] == "route"]


# --- delegation -------------------------------------------------------------

def test_large_context_delegates_when_agents_installed():
    install.install_agents()
    statusline.run(payload(ctx_tokens=150_000))  # Opus, big context
    out = prompt.run({"session_id": "s1", "prompt": "what is a closure?"})
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "claudepilot-quick" in ctx and "systemMessage" not in out
    assert _route_events()[-1]["action"] == "delegate"


def test_large_context_without_agents_just_stays():
    statusline.run(payload(ctx_tokens=150_000))
    out = prompt.run({"session_id": "s1", "prompt": "what is a closure?"})
    assert "claudepilot-quick" not in json.dumps(out)
    assert _route_events()[-1]["action"] == "stay"


def test_followup_yes_does_not_switch_to_haiku():
    statusline.run(payload())  # Opus
    prompt.run({"session_id": "s1", "prompt": "design the architecture for the billing service"})
    out = prompt.run({"session_id": "s1", "prompt": "yes"})
    assert "systemMessage" not in out
    assert _route_events()[-1]["rule"] == "context-followup"


# --- agents -----------------------------------------------------------------

def test_agents_install_respects_user_files(isolated_dirs):
    target = install.agents_dir()
    target.mkdir(parents=True)
    (target / "claudepilot-deep.md").write_text("my own agent")
    assert install.install_agents() == ["claudepilot-quick"]
    assert (target / "claudepilot-deep.md").read_text() == "my own agent"
    assert install.uninstall_agents() == ["claudepilot-quick"]
    assert (target / "claudepilot-deep.md").exists()


def test_agent_files_have_models():
    for name, model in (("claudepilot-quick", "haiku"), ("claudepilot-deep", "opus")):
        text = install._agent_source(name)
        assert f"name: {name}" in text and f"model: {model}" in text


# --- PreModelSwitch ---------------------------------------------------------

def test_pre_model_switch_allows_only_our_switch():
    ev = {"session_id": "s1", "from_model": "claude-opus-5-5", "to_model": "claude-haiku-5-5"}
    assert model_switch.pre_run(ev) is None  # user-initiated: normal flow
    state.write_json(model_switch.switching_path("s1"), {"tier": "haiku", "ts": time.time()})
    assert model_switch.pre_run(ev)["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert model_switch.pre_run({**ev, "to_model": "claude-sonnet-5-5"}) is None  # wrong target
    state.write_json(model_switch.switching_path("s1"), {"tier": "haiku", "ts": time.time() - 120})
    assert model_switch.pre_run(ev) is None  # stale marker
    model_switch.run(ev)
    assert not model_switch.switching_path("s1").exists()  # cleared once confirmed


# --- SQLite history ---------------------------------------------------------

def test_sync_is_incremental_and_stats_track_followed_advice():
    now = time.time()
    state.append_event("route", pane="p", action="switch", recommended="haiku", current="opus")
    state.append_event("model_switch", pane="p", to_model="claude-haiku-5-5")
    state.append_event("route", pane="p", action="switch", recommended="opus", current="haiku")
    state.append_event("tokens", pane="p", level="caveman")
    state.append_usage(40, 10, ts=now)
    assert db.sync() == (4, 1)
    assert db.sync() == (0, 0)  # nothing new
    st = db.stats(days=1)
    assert st.switch_advice == 2 and st.followed == 1 and st.follow_rate == 0.5
    assert st.token_levels["caveman"] == 1
    assert st.days[-1].prompts == 2 and st.days[-1].peak_five_hour == 40


def test_history_survives_log_rotation():
    state.append_event("route", pane="p", action="match", recommended="opus", current="opus")
    db.sync()
    (state.state_dir() / "events.jsonl").unlink()
    assert db.stats(days=1).days[-1].prompts == 1


# --- plugin -----------------------------------------------------------------

def test_plugin_agents_match_package_agents():
    for name in install.AGENT_NAMES:
        assert (PLUGIN / "agents" / f"{name}.md").read_text() == install._agent_source(name)


def test_plugin_hooks_cover_every_event():
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())["hooks"]
    assert set(hooks) == set(install.HOOK_EVENTS)
    for event, name in install.HOOK_EVENTS.items():
        cmd = hooks[event][0]["hooks"][0]["command"]
        assert cmd.endswith(f"claudepilot-hook {name}") and "|| exit 0" in cmd
        if event in install.MATCHERS:
            assert hooks[event][0]["matcher"] == install.MATCHERS[event]


def test_plugin_versions_match_package():
    from claudepilot import __version__

    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
    assert __version__.startswith(manifest["version"])


def test_plugin_enabled_detection():
    assert install.plugin_enabled({"enabledPlugins": {"claudepilot@claudepilot": True}})
    assert not install.plugin_enabled({"enabledPlugins": {"claudepilot@claudepilot": False}})
    assert not install.plugin_enabled({"enabledPlugins": {"other@x": True}})


# --- auto-mode regressions found against real Claude Code --------------------

def test_loop_guard_hash_ignores_paste_wrappers_and_whitespace():
    a = prompt.prompt_hash("what is a closure in python?")
    wrapped = '<pasted_content id="b39c">what is a closure   in python?</pasted_content>'
    assert prompt.prompt_hash(wrapped) == a
    assert prompt.prompt_hash("what is a closure in java?") != a


def test_auto_switch_refuses_risky_prompts():
    now = time.time()
    assert prompt.can_auto_switch("what is a closure?", {}, now) is None
    assert "in flight" in prompt.can_auto_switch("what is x?", {"ts": now - 5}, now)
    assert prompt.can_auto_switch("what is x?", {"ts": now - 600}, now) is None  # stale
    assert "multi-line" in prompt.can_auto_switch("line one\nline two", {}, now)
    assert "/model" in prompt.can_auto_switch("what is x? /model haiku", {}, now)
    assert "too long" in prompt.can_auto_switch("x" * 5000, {}, now)


def test_auto_mode_never_cascades(monkeypatch):
    """A second prompt while a switch is in flight gets advice, never another block."""
    statusline.run(payload())
    state.write_json(state.state_dir() / "router_mode.json", {"mode": "auto"})
    monkeypatch.setenv("TMUX_PANE", "%7")
    started = []
    monkeypatch.setattr(prompt, "_start_auto_switch", lambda *a: started.append(a) or True)
    statusline.run(payload())  # state for pane %7
    first = prompt.run({"session_id": "s1", "prompt": "what is a closure?"})
    assert first["decision"] == "block" and len(started) == 1
    second = prompt.run({"session_id": "s1", "prompt": "what is a monad?"})
    assert "decision" not in second and len(started) == 1
    skipped = [e for e in state.read_events() if e["kind"] == "auto_route"]
    assert skipped[-1]["status"] == "skipped"


def test_restore_saved_model_only_undoes_our_change(isolated_dirs):
    from claudepilot.hooks import resubmit

    path = isolated_dirs / "claude" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"model": "haiku", "theme": "dark"}))  # /model haiku just ran
    assert resubmit.restore_saved_model("opus", "haiku")
    assert json.loads(path.read_text()) == {"model": "opus", "theme": "dark"}

    path.write_text(json.dumps({"model": "haiku"}))
    assert resubmit.restore_saved_model(resubmit._MISSING, "haiku")
    assert json.loads(path.read_text()) == {}  # there was no saved model before

    path.write_text(json.dumps({"model": "sonnet"}))  # user changed it meanwhile
    assert not resubmit.restore_saved_model("opus", "haiku")
    assert json.loads(path.read_text()) == {"model": "sonnet"}


def test_first_prompt_uses_saved_default_model(isolated_dirs):
    settings = isolated_dirs / "claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"model": "opus"}))
    out = prompt.run({"session_id": "fresh", "prompt": "what is a closure?"})  # no statusline yet
    assert "Haiku" in out["systemMessage"]
    ev = _route_events()[-1]
    assert ev["current"] == "opus" and ev["action"] == "switch"
    assert ev["note"] == "model assumed from saved default"


def test_auto_outside_tmux_logs_why():
    statusline.run(payload())
    out = prompt.run({"session_id": "s1", "prompt": "what is a closure?"})
    assert "systemMessage" in out
    skipped = [e for e in state.read_events() if e["kind"] == "auto_route"][-1]
    assert skipped["status"] == "skipped" and "outside the cockpit" in skipped["note"]
