"""Tiny interactive-panel loop: raw key input + Rich Live, no extra dependencies.

A panel provides `render(console) -> renderable` and `on_key(key) -> action`. An action
is None (just redraw) or a callable, which runs with the terminal handed back (Live
stopped, cooked mode restored), e.g. to open an editor in this pane.
"""

from __future__ import annotations

import contextlib
import os
import select
import subprocess
import sys
import termios
import time
import tty
from collections.abc import Callable
from typing import Protocol

from rich.console import Console, RenderableType
from rich.live import Live

ESCAPES = {
    "[A": "up", "[B": "down", "[C": "right", "[D": "left",
    "OA": "up", "OB": "down", "OC": "right", "OD": "left",
    "[H": "home", "[F": "end", "OH": "home", "OF": "end", "[1~": "home", "[4~": "end",
    "[5~": "pageup", "[6~": "pagedown", "[Z": "shift-tab",
    # Focus reporting (enabled with ESC[?1004h; tmux forwards it with focus-events on).
    "[I": "focus-in", "[O": "focus-out",
}
PLAIN = {"\r": "enter", "\n": "enter", "\t": "tab", "\x7f": "backspace", " ": "space"}

Action = Callable[[], None] | None


class Panel(Protocol):
    focused: bool

    def render(self, console: Console) -> RenderableType: ...
    def on_key(self, key: str) -> Action: ...
    def tick(self, now: float) -> None: ...


# --- moving between tmux panes ------------------------------------------------

EDGE = {"up": "pane_at_top", "down": "pane_at_bottom", "left": "pane_at_left", "right": "pane_at_right"}
FLAG = {"up": "-U", "down": "-D", "left": "-L", "right": "-R"}


def _tmux(*args: str) -> str | None:
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        return None
    try:
        proc = subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def move_focus(direction: str) -> bool:
    """Focus the neighbouring tmux pane. Never wraps around an edge (tmux would)."""
    pane = os.environ.get("TMUX_PANE")
    if not pane or direction not in FLAG:
        return False
    if _tmux("display-message", "-p", "-t", pane, f"#{{{EDGE[direction]}}}") == "1":
        return False
    return _tmux("select-pane", "-t", pane, FLAG[direction]) is not None


def focus_pane(target: str) -> bool:
    return bool(target) and _tmux("select-pane", "-t", target) is not None


def is_active() -> bool:
    pane = os.environ.get("TMUX_PANE")
    return bool(pane) and _tmux("display-message", "-p", "-t", pane, "#{pane_active}") == "1"


def parse_keys(data: str) -> list[str]:
    """Turn raw terminal input into key names ('up', 'enter', 'q', ...)."""
    keys: list[str] = []
    i = 0
    while i < len(data):
        ch = data[i]
        if ch == "\x1b":
            for seq, name in ESCAPES.items():
                if data.startswith(seq, i + 1):
                    keys.append(name)
                    i += 1 + len(seq)
                    break
            else:
                # Unknown sequence: skip it whole (ESC [ ... final byte) or a lone ESC.
                j = i + 1
                if j < len(data) and data[j] in "[O":
                    j += 1
                    while j < len(data) and not ("@" <= data[j] <= "~"):
                        j += 1
                    j += 1
                    i = j
                else:
                    keys.append("escape")
                    i += 1
        elif ch in PLAIN:
            keys.append(PLAIN[ch])
            i += 1
        elif ch.isprintable():
            keys.append(ch)
            i += 1
        else:
            i += 1
    return keys


@contextlib.contextmanager
def cbreak(fd: int):
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield old
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


FOCUS_ON, FOCUS_OFF = "\x1b[?1004h", "\x1b[?1004l"


FOCUS_POLL_SECONDS = 10.0


def run(panel: Panel, refresh_seconds: float = 1.0) -> None:
    """Event loop. Redraws only when something visible changed: a key, a focus change, a
    resize, or the panel's `signature()` (if it has one). Idle panels cost a select() call."""
    console = Console()
    fd = sys.stdin.fileno()
    if not os.isatty(fd):
        panel.tick(time.time())
        console.print(panel.render(console))
        return
    signature = getattr(panel, "signature", None)

    while True:  # re-entered after an action that borrowed the terminal
        pending: Action = None
        panel.focused = is_active()
        last_poll = time.time()
        panel.tick(last_poll)
        last_state = (console.size, signature(last_poll) if signature else None)
        with cbreak(fd), Live(panel.render(console), console=console, screen=True,
                              auto_refresh=False, vertical_overflow="crop") as live:
            sys.stdout.write(FOCUS_ON)
            sys.stdout.flush()
            try:
                while pending is None:
                    ready, _, _ = select.select([fd], [], [], refresh_seconds)
                    now = time.time()
                    dirty = False
                    if ready:
                        data = os.read(fd, 1024).decode(errors="ignore")
                        for key in parse_keys(data):
                            dirty = True
                            if key in ("focus-in", "focus-out"):
                                panel.focused = key == "focus-in"
                                continue
                            panel.focused = True  # keys only reach the active pane
                            pending = panel.on_key(key)
                            if pending is not None:
                                break
                    elif now - last_poll >= FOCUS_POLL_SECONDS:
                        panel.focused = is_active()  # safety net if a focus event was lost
                        last_poll = now
                    current = (console.size, signature(now) if signature else None)
                    if not dirty and signature and current == last_state:
                        continue  # nothing visible changed: skip tick + redraw
                    last_state = current
                    panel.tick(now)
                    live.update(panel.render(console), refresh=True)
            finally:
                sys.stdout.write(FOCUS_OFF)
                sys.stdout.flush()
        pending()
