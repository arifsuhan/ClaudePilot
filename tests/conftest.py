import pytest


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """Never touch the developer's real ~/.claude or state dirs."""
    monkeypatch.setenv("CLAUDEPILOT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDEPILOT_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CLAUDEPILOT_CLAUDE_SETTINGS", str(tmp_path / "claude" / "settings.json"))
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.delenv("TMUX", raising=False)
    return tmp_path


def payload(model="claude-opus-5-5", name="Opus", five=None, week=None, ctx_tokens=1000,
            resets5=None, resets7=None):
    data = {
        "session_id": "s1",
        "model": {"id": model, "display_name": name},
        "context_window": {"used_percentage": 12, "total_input_tokens": ctx_tokens},
    }
    limits = {}
    if five is not None:
        limits["five_hour"] = {"used_percentage": five, "resets_at": resets5}
    if week is not None:
        limits["seven_day"] = {"used_percentage": week, "resets_at": resets7}
    if limits:
        data["rate_limits"] = limits
    return data
