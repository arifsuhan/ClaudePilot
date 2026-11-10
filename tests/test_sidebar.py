import time
from pathlib import Path

from conftest import payload
from rich.console import Console

from claudepilot import sidebar
from claudepilot.explorer.git import parse_porcelain
from claudepilot.hooks import statusline


def _text(renderable):
    console = Console(width=44, record=True, color_system=None)
    console.print(renderable)
    return console.export_text()


def test_sidebar_renders_waiting_state():
    out = _text(sidebar.render(None, Path("/tmp/proj"), parse_porcelain("## main\n"), time.time()))
    assert "waiting for Claude Code" in out and "main" in out


def test_sidebar_renders_limits():
    now = time.time()
    statusline.run(payload(five=55, week=20, resets5=now + 3600, resets7=now + 86400))
    out = _text(sidebar.render("s1", Path("/tmp/proj"), parse_porcelain("## main\n M a.py\n"), now))
    assert "Opus" in out and "55%" in out and "a.py" in out


def test_sidebar_shows_cached_limits_but_not_other_sessions_model():
    now = time.time()
    statusline.run(payload(five=55, week=20, resets5=now + 3600, resets7=now + 86400))
    out = _text(sidebar.render("other-pane", Path("/tmp/proj"), parse_porcelain("## main\n"), now))
    assert "55%" in out and "20%" in out
    assert "claude-opus-5-5" not in out and "waiting" in out


def test_sidebar_has_control_and_guide():
    out = _text(sidebar.render(None, Path("/tmp/proj"), parse_porcelain("## main\n"), time.time(),
                               secrets=[".env"]))
    assert "CONTROL" in out and "Router AUTO" in out and "Tokens AUTO" in out
    assert "GUIDE" in out and ".env not gitignored" in out
