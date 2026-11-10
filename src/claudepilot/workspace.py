"""tmux session management: one cockpit session per project directory."""

from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path


class TmuxError(RuntimeError):
    pass


def session_name(cwd: Path) -> str:
    digest = hashlib.sha1(str(cwd.resolve()).encode()).hexdigest()[:6]
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in cwd.name)[:24]
    return f"pilot-{safe}-{digest}"


def _tmux(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    if not shutil.which("tmux"):
        raise TmuxError("tmux is not installed. Install it with your package manager.")
    proc = subprocess.run(["tmux", *args], capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise TmuxError(proc.stderr.strip() or f"tmux {' '.join(args)} failed")
    return proc


def exists(name: str) -> bool:
    return _tmux("has-session", "-t", f"={name}", check=False).returncode == 0


NAV_KEYS = {f"{mod}-{arrow}": flag for mod in ("C", "M")
            for arrow, flag in (("Left", "-L"), ("Right", "-R"), ("Up", "-U"), ("Down", "-D"))}


def bind_nav_keys() -> list[str]:
    """Ctrl+arrows (and Alt+arrows) move between panes, without the tmux prefix.

    tmux key bindings are server-wide, so each one only acts inside a ClaudePilot session
    (one with @claudepilot_claude_pane set) and passes the key through everywhere else.
    Keys the user has already bound to something else are left alone. Plain arrows are not
    used: Claude Code needs them for its own cursor and history. Inside the cockpit, Claude
    Code's Ctrl+Left/Right word jumps give way to this; Alt+B / Alt+F still jump words.
    """
    existing = _tmux("list-keys", "-T", "root", check=False).stdout.splitlines()
    bound = []
    for key, direction in NAV_KEYS.items():
        taken = [line for line in existing if f" {key} " in f"{line} "]
        if any("@claudepilot" not in line for line in taken):
            continue
        _tmux("bind-key", "-n", key, "if-shell", "-F", "#{@claudepilot_claude_pane}",
              f"select-pane {direction}", f"send-keys {key}")
        bound.append(key)
    return bound


def _panel_cmd(kind: str, claude_pane: str, cwd: Path) -> str:
    return " ".join(shlex.quote(p) for p in [
        sys.executable, "-m", "claudepilot", "panel", kind, "--pane", claude_pane, "--cwd", str(cwd)])


def create(cwd: Path, claude_command: str, sidebar_width: int, close_with_claude: bool = True,
           nav_keys: bool = True, mouse: bool = True) -> str:
    name = session_name(cwd)
    if exists(name):
        return name
    kill = f"tmux kill-session -t {shlex.quote('=' + name)}"
    # Pane 1: Claude Code (the main pane). When Claude Code exits (/exit, Ctrl-D,
    # crash), the shell continues to `kill-session`, closing the sidebar too.
    pane_cmd = f"{claude_command}; {kill}" if close_with_claude else claude_command
    claude_pane = _tmux(
        "new-session", "-d", "-s", name, "-c", str(cwd), "-x", "200", "-y", "50",
        "-P", "-F", "#{pane_id}", pane_cmd,
    ).stdout.strip()
    if close_with_claude:
        # Closing the pane through tmux (Ctrl-b x) kills the process, so the command
        # above never runs: after any pane is killed, end the session if the Claude
        # pane is gone. (No #{formats} here: run-shell would expand them at hook time.)
        check = f"tmux list-panes -t {shlex.quote(claude_pane)} >/dev/null 2>&1 || {kill}"
        _tmux("set-hook", "-t", name, "after-kill-pane", f"run-shell {shlex.quote(check)}")
    # Four panels stacked on the left, each its own pane so it can take focus and keys.
    def split(flags: list[str], target: str, size: str, kind: str) -> str:
        pane = _tmux("split-window", *flags, "-t", target, "-l", size, "-c", str(cwd),
                     "-P", "-F", "#{pane_id}", _panel_cmd(kind, claude_pane, cwd)).stdout.strip()
        _tmux("select-pane", "-t", pane, "-T", kind)
        return pane

    usage = split(["-h", "-b"], claude_pane, str(sidebar_width), "usage")
    control = split(["-v"], usage, "72%", "control")
    guide = split(["-v"], control, "78%", "guide")
    split(["-v"], guide, "62%", "files")
    _tmux("set-option", "-t", name, "status-left", " ClaudePilot ")
    _tmux("set-option", "-t", name, "status-left-length", "20")
    _tmux("set-option", "-t", name, "@claudepilot_claude_pane", claude_pane)
    if nav_keys:
        bind_nav_keys()
        _tmux("set-option", "-t", name, "status-right", " Ctrl+arrows: switch panel ")
    if mouse:
        _tmux("set-option", "-t", name, "mouse", "on")  # click a panel to focus it
    # Lets panels highlight instantly when they gain or lose focus.
    _tmux("set-option", "-s", "focus-events", "on", check=False)
    _tmux("select-pane", "-t", claude_pane)
    return name


def attach(name: str) -> None:
    """Attach (or switch-client when already inside tmux). Replaces this process."""
    if os.environ.get("TMUX"):
        _tmux("switch-client", "-t", name)
        return
    tmux = shutil.which("tmux")
    assert tmux
    os.execv(tmux, [tmux, "attach-session", "-t", name])


def stop(name: str) -> bool:
    if not exists(name):
        return False
    _tmux("kill-session", "-t", f"={name}")
    return True
