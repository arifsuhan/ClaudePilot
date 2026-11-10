import subprocess
import time
from pathlib import Path

from conftest import payload
from rich.console import Console

from claudepilot import panels, state, tui
from claudepilot.explorer.tree import FileTree, list_files
from claudepilot.hooks import statusline

FILES = ["README.md", "src/app.py", "src/core/db.py", "src/core/io.py", "tests/test_app.py"]


def text_of(renderable, width=42, height=16):
    console = Console(width=width, height=height, record=True, color_system=None)
    console.print(renderable)
    return console.export_text()


# --- keys --------------------------------------------------------------------

def test_parse_keys():
    assert tui.parse_keys("\x1b[A\x1b[B\x1b[C\x1b[D") == ["up", "down", "right", "left"]
    assert tui.parse_keys("\x1bOA\r\tq1") == ["up", "enter", "tab", "q", "1"]
    assert tui.parse_keys("\x1b[5~\x1b[6~\x1b[H\x1b[F") == ["pageup", "pagedown", "home", "end"]
    assert tui.parse_keys("\x1b[1;3Dj") == ["j"]  # unknown sequence (Alt+Left) is swallowed
    assert tui.parse_keys("\x1b") == ["escape"]


# --- file tree ---------------------------------------------------------------

def names(tree):
    return [("  " * r.depth) + r.name for r in tree.rows]


def test_tree_starts_collapsed_dirs_first():
    tree = FileTree(FILES)
    assert names(tree) == ["src", "tests", "README.md"]
    assert tree.current.path == "src"


def test_tree_expand_collapse_and_cursor():
    tree = FileTree(FILES)
    tree.toggle()  # open src
    assert names(tree) == ["src", "  core", "  app.py", "tests", "README.md"]
    tree.move(1)
    tree.toggle(True)  # open src/core
    tree.move(1)
    assert tree.current.path == "src/core/db.py" and tree.current.depth == 2
    tree.collapse_or_parent()  # file -> parent dir
    assert tree.current.path == "src/core"
    tree.collapse_or_parent()  # open dir -> collapse
    assert "    db.py" not in names(tree)
    tree.move(-99)
    assert tree.cursor == 0
    tree.move(99)
    assert tree.current.path == "README.md"


def test_tree_update_keeps_expansion_and_cursor():
    tree = FileTree(FILES)
    tree.toggle()
    tree.move(2)
    assert tree.current.path == "src/app.py"
    tree.update([*FILES, "src/new.py"], {"src/new.py": "?"})
    assert tree.current.path == "src/app.py" and "  new.py" in names(tree)
    tree.update(["README.md"], {})  # everything else deleted
    assert tree.current.path == "README.md"


def test_tree_statuses_and_untracked_dirs():
    tree = FileTree([*FILES, "docs/a.md"], {"src/core/db.py": "M", "docs/": "?"})
    by = {r.path: r for r in tree.rows}
    assert by["src"].status == "*" and by["docs"].status == "*" and by["tests"].status == ""
    tree.cursor_path = "docs"
    tree.toggle()
    assert {r.path: r.status for r in tree.rows}["docs/a.md"] == "?"


def test_tree_window_scrolls_with_cursor():
    tree = FileTree([f"f{i:02d}.txt" for i in range(30)])
    rows, cur = tree.window(5)
    assert [r.name for r in rows][0] == "f00.txt" and cur == 0
    tree.move(12)
    rows, cur = tree.window(5)
    assert rows[cur].name == "f12.txt" and cur == 4
    tree.move_to(0)
    rows, cur = tree.window(5)
    assert rows[0].name == "f00.txt"


def test_list_files_respects_gitignore(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "a.py").write_text("")
    (tmp_path / "secret.log").write_text("")
    (tmp_path / ".gitignore").write_text("*.log\n")
    assert list_files(tmp_path) == [".gitignore", "a.py"]


def test_list_files_outside_git(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "x.txt").write_text("")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "y.js").write_text("")
    assert list_files(tmp_path) == ["sub/x.txt"]


# --- panels ------------------------------------------------------------------

def test_usage_panel_tabs_and_keys():
    now = time.time()
    statusline.run(payload(five=40, week=10, resets5=now + 3600, resets7=now + 86400))
    panel = panels.UsagePanel("s1")
    console = Console(width=42, height=16)
    out = text_of(panel.render(console))
    assert "Usage" in out and "40%" in out and "Opus" in out
    panel.on_key("left")
    assert panels.TABS[panel.tab] == "Config"
    out = text_of(panel.render(console))
    assert "Router" in out and "Guard" in out
    panel.on_key("1")
    assert "claude-opus-5-5" in text_of(panel.render(console), width=60)
    panel.on_key("4")
    assert "0 today" in text_of(panel.render(console))
    state.append_event("route", pane="s1", action="match", recommended="opus", current="opus")
    panel._stats = None
    assert "1 today" in text_of(panel.render(console))
    panel.on_key("right")
    assert panels.TABS[panel.tab] == "Status"  # wraps around


def test_files_panel_navigation_and_open(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x")
    (tmp_path / "README.md").write_text("x")
    monkeypatch.setattr(panels, "editor_command", lambda configured=None: ["vim"])
    panel = panels.FilesPanel(tmp_path)
    console = Console(width=42, height=12)
    out = text_of(panel.render(console), height=12)
    assert "+ src/" in out and "README.md" in out and "1/2" not in out  # hint only when focused
    panel.focused = True
    assert "1/2" in text_of(panel.render(console), height=12)

    assert panel.on_key("enter") is None  # Enter on a directory expands it
    assert "- src/" in text_of(panel.render(console), height=12)
    panel.on_key("down")
    assert panel.tree.current.path == "src/app.py"

    ran = []
    real_run = subprocess.run
    monkeypatch.setattr(panels.subprocess, "run",
                        lambda argv, **kw: ran.append(argv) if argv[0] == "vim" else real_run(argv, **kw))
    action = panel.on_key("enter")  # not inside tmux: runs in this pane via the returned action
    assert callable(action)
    action()
    assert ran[0] == ["vim", "--", "src/app.py"]


def test_files_panel_without_editor(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("x")
    monkeypatch.setattr(panels, "editor_command", lambda configured=None: None)
    panel = panels.FilesPanel(tmp_path)
    assert panel.on_key("enter") is None and "No editor" in panel.message


def test_info_panel_renders(tmp_path):
    out = text_of(panels.InfoPanel(None, Path(tmp_path)).render(Console(width=42, height=20)), height=30)
    assert "CONTROL" in out and "GUIDE" in out


def test_arrows_stay_inside_panels(monkeypatch):
    moved = []
    monkeypatch.setattr(panels.tui, "move_focus", lambda d: moved.append(d))
    focused = []
    monkeypatch.setattr(panels.tui, "focus_pane", lambda t: focused.append(t) or True)
    usage = panels.UsagePanel("%0")
    for key in ("up", "down", "left", "right"):
        usage.on_key(key)
    status = panels.StatusPanel("%0", Path("."), "guide")
    for key in ("up", "down", "left", "right", "enter"):
        status.on_key(key)
    assert moved == []  # Ctrl+arrows (tmux) switch panels; plain arrows never do
    status.on_key("escape")
    assert focused == ["%0"]


def test_usage_tabs_wrap_with_plain_arrows():
    usage = panels.UsagePanel(None)
    usage.tab = 0
    usage.on_key("left")
    assert panels.TABS[usage.tab] == "Stats"
    usage.on_key("right")
    assert panels.TABS[usage.tab] == "Status"


def test_control_and_guide_render_separately(tmp_path):
    console = Console(width=42, height=14)
    control = text_of(panels.StatusPanel(None, tmp_path, "control").render(console), height=14)
    guide = text_of(panels.StatusPanel(None, tmp_path, "guide").render(console), height=14)
    assert "CONTROL" in control and "GUIDE" not in control
    assert "GUIDE" in guide and "CONTROL" not in guide


def test_focus_events_parsed():
    assert tui.parse_keys("\x1b[I\x1b[O") == ["focus-in", "focus-out"]


def test_nav_bindings_cover_ctrl_and_alt():
    from claudepilot import workspace

    assert {"C-Up", "C-Down", "C-Left", "C-Right", "M-Up", "M-Left"} <= set(workspace.NAV_KEYS)


# --- CONTROL panel -------------------------------------------------------------

def test_control_panel_changes_modes_with_keys(tmp_path):
    from claudepilot import config

    panel = panels.ControlPanel(None, tmp_path)
    panel.focused = True
    assert config.router_mode() == "auto"
    panel.on_key("right")  # Router: auto -> off (wraps)
    assert config.router_mode() == "off"
    panel.on_key("enter")  # off -> advise
    assert config.router_mode() == "advise"
    panel.on_key("down")
    panel.on_key("left")  # Tokens: auto -> off
    assert config.token_mode() == "off"
    panel.on_key("3")
    panel.on_key("right")  # Guard: ask -> deny
    assert config.guard_mode() == "deny"
    out = text_of(panel.render(Console(width=42, height=14)), height=14)
    assert "1 Router" in out and "[ADVISE]" in out and "[OFF]" in out and "[DENY]" in out
    assert "Guard: deny" in out


def test_control_panel_up_down_wrap(tmp_path):
    panel = panels.ControlPanel(None, tmp_path)
    panel.on_key("up")
    assert panel.row == 2
    panel.on_key("down")
    assert panel.row == 0


def test_explain_routing_decisions():
    base = {"recommended": "haiku", "current": "opus", "reason": "short prompt"}
    assert "on opus" in panels.explain({**base, "action": "match", "current": "opus"}, None)[0]
    stay = panels.explain({**base, "action": "stay", "note": "450k tokens in context; ..."}, None)[0]
    assert "kept opus, not haiku" in stay and "450k" in stay
    assert "claudepilot-quick" in panels.explain({**base, "action": "delegate"}, None)[0]
    assert "switched opus -> haiku" in panels.explain({**base, "action": "switch"}, {"status": "ok"})[0]
    follow = {"status": "skipped", "note": "outside the cockpit"}
    skipped = panels.explain({**base, "action": "switch"}, follow)
    assert "outside the cockpit" in skipped[0]


def test_control_panel_shows_last_prompts(tmp_path):
    state.append_event("route", action="stay", recommended="haiku", current="opus",
                       reason="short prompt", note="450k tokens in context")
    panel = panels.ControlPanel(None, tmp_path)
    out = text_of(panel.render(Console(width=60, height=14)), width=60, height=14)
    assert "Last prompts" in out and "kept opus, not haiku: 450k ctx" in out
