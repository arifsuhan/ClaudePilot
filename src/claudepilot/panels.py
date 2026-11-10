"""The cockpit's left-hand panels, each its own tmux pane (see workspace.create).

  usage    tabs Status | Config | Usage | Stats
  control  router / token / guard modes and recent routing
  guide    contextual warnings + rotating tips
  files    scrollable file tree; Enter opens the file in the editor

Ctrl+arrows switch panels (tmux bindings, see workspace.bind_nav_keys); Alt+arrows and a
mouse click do too. Inside a panel, plain arrows and Enter act on that panel only.
Esc jumps back to Claude Code.

The usage panel mirrors what Claude Code reports through its statusline. It is not Claude
Code's own /usage dialog, which can't be embedded; type /usage in the Claude pane for that.
"""

from __future__ import annotations

import contextlib
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from claudepilot import __version__, config, state, tui
from claudepilot.explorer import tree as filetree
from claudepilot.explorer.git import GitStatus, git_dir_mtime, read_status, unignored_secrets
from claudepilot.paths import claude_settings_path, config_dir, safe_key
from claudepilot.usage.limits import format_countdown

# Heavier modules (sidebar -> guide/tokens/yaml, storage.db -> sqlite3, install) are
# imported inside the panels that use them, so e.g. the Files panel never loads them.

TABS = ("Status", "Config", "Usage", "Stats")
STATS_REFRESH_SECONDS = 60.0
GIT_STATUS_SECONDS = 10.0   # worktree edits that don't touch .git/index
FILE_LIST_SECONDS = 30.0    # new/removed files that don't touch .git/index
SECRET_SCAN_SECONDS = 30.0
HISTORY_SYNC_SECONDS = 300.0


def _state_files(pane: str | None) -> list[Path]:
    base = state.state_dir()
    files = [base / "latest.json", base / "limits.json", base / "events.jsonl",
             base / "router_mode.json", base / "tokens_mode.json", base / "guard_mode.json"]
    if pane:
        key = safe_key(pane)
        files += [base / "panes" / f"{key}.json", base / "panes" / f"{key}.meta.json"]
    return files


def navigate(key: str, claude_pane: str | None) -> bool:
    """Esc returns focus to Claude Code. True if handled."""
    if key == "escape" and claude_pane:
        tui.focus_pane(claude_pane)
        return True
    return False


def frame(body: RenderableType, title: str, focused: bool, color: str, height: int | None = None,
          subtitle: str | None = None) -> Panel:
    """Panel chrome. The focused panel gets a reversed title and a bright border."""
    label = Text(f" {title} ", style="reverse bold" if focused else "bold")
    return Panel(body, title=label, title_align="left", border_style=f"bold {color}" if focused else "grey50",
                 subtitle=subtitle if focused else None, subtitle_align="right", height=height)


def _kv(rows: list[tuple[str, Any]]) -> Table:
    table = Table.grid(padding=(0, 1), expand=True)
    table.add_column(style="bold", no_wrap=True, width=7)  # fixed: only the value may shrink
    table.add_column(overflow="ellipsis", no_wrap=True, ratio=1)
    for key, value in rows:
        table.add_row(key, value if isinstance(value, Text) else Text(str(value)))
    return table


# --------------------------------------------------------------------------- usage

class UsagePanel:
    focused = False

    def __init__(self, pane: str | None, tab: str = "Usage"):
        self.pane = pane
        self.tab = TABS.index(tab) if tab in TABS else 2
        self.now = time.time()
        self._stats: Any = None
        self._stats_at = 0.0

    def tick(self, now: float) -> None:
        self.now = now

    def signature(self, now: float) -> tuple:
        files = _state_files(self.pane) + [state.state_dir() / "usage_history.jsonl"]
        # Countdowns show minutes; Stats refreshes every STATS_REFRESH_SECONDS.
        return (self.tab, self.focused, state.file_sig(*files), int(now // 30))

    def on_key(self, key: str) -> tui.Action:
        if key in ("right", "tab", "l"):
            self.tab = (self.tab + 1) % len(TABS)
        elif key in ("left", "shift-tab", "h"):
            self.tab = (self.tab - 1) % len(TABS)
        elif key in ("1", "2", "3", "4"):
            self.tab = int(key) - 1
        else:
            navigate(key, self.pane)
        return None

    # -- data
    def _record(self) -> dict[str, Any]:
        return state.read_session_state(self.pane, fallback=self.pane is None)

    def _stats_cached(self) -> Any:
        if self._stats is None or self.now - self._stats_at >= STATS_REFRESH_SECONDS:
            from claudepilot.storage import db

            with contextlib.suppress(Exception):  # stats are best-effort; never kill the UI
                self._stats = db.stats(7, self.now)
            self._stats_at = self.now
        return self._stats

    # -- tabs
    def status_tab(self) -> RenderableType:
        record = self._record()
        p = record.get("payload") or {}
        if not p:
            return Text("Waiting for Claude Code...\nSend a prompt, or run `claudepilot doctor`.",
                        style="dim")
        model, cost, cache = p.get("model") or {}, p.get("cost") or {}, p.get("prompt_cache") or {}
        meta = state.read_json(
            state.state_dir() / "panes" / f"{safe_key(self.pane)}.meta.json") if self.pane else {}
        repo = (p.get("workspace") or {}).get("repo") or {}
        thinking = "on" if (p.get("thinking") or {}).get("enabled") else "off"
        rows: list[tuple[str, Any]] = [
            ("Claude", f"Code {p.get('version', '?')}"),
            ("Model", f"{model.get('display_name', '?')} ({model.get('id', '?')})"),
            ("Effort", f"{(p.get('effort') or {}).get('level', '?')} . thinking {thinking}"
                       + (" . fast" if p.get("fast_mode") else "")),
            ("Perms", meta.get("permission_mode") or "(after first prompt)"),
        ]
        if cost.get("total_duration_ms") is not None:
            rows.append(("Session", f"{format_countdown(cost['total_duration_ms'] / 1000)} . "
                                    f"+{cost.get('total_lines_added', 0)}/-"
                                    f"{cost.get('total_lines_removed', 0)} lines"))
        if cache:
            hit = cache.get("hit_ratio")
            rows.append(("Cache", f"{'warm' if cache.get('warm') else 'cold'}"
                                  + (f" . hit {hit * 100:.0f}%" if hit is not None else "")
                                  + (f" . ttl {cache['ttl']}" if cache.get("ttl") else "")))
        if repo.get("name"):
            rows.append(("Repo", f"{repo.get('owner', '?')}/{repo['name']}"))
        try:
            from claudepilot import install

            settings = install.load_settings(claude_settings_path())
            hooks_ok = install.plugin_enabled(settings) or all(install.is_installed(settings).values())
        except (OSError, ValueError):
            hooks_ok = False
        hooks = ("ok", "green") if hooks_ok else ("outdated", "red")
        rows.append(("Pilot", Text.assemble(f"{__version__} . hooks ", hooks)))
        if not hooks_ok:
            rows.append(("", Text("run: claudepilot install", style="red")))
        age = self.now - record.get("received_at", self.now)
        if age > 120:
            rows.append(("Data", Text(f"{format_countdown(age)} old", style="dim")))
        return _kv(rows)

    def config_tab(self) -> RenderableType:
        cfg = config.load()
        try:
            from claudepilot import install

            default_model = install.load_settings(claude_settings_path()).get("model", "(not set)")
        except (OSError, ValueError):
            default_model = "?"
        tk, rt = cfg["tokens"], cfg["router"]
        rows = [
            ("Router", f"{config.router_mode()}   (off|advise|auto)"),
            ("Tokens", f"{config.token_mode()} . caveman {tk['caveman_usage']}% use/"
                       f"{tk['caveman_context']}% ctx"),
            ("Guard", f"{config.guard_mode()}   (off|ask|deny)"),
            ("Deleg.", f"{'on' if rt.get('delegate') else 'off'} above "
                       f"{int(rt['switch_cost_tokens']) // 1000}k context tokens"),
            ("Alerts", f"at {', '.join(str(t) for t in cfg['alerts']['thresholds'])}%"),
            ("Default", f"{default_model} (Claude Code settings)"),
        ]
        return Group(
            _kv(rows),
            Text("Set: claudepilot router|tokens|guard", style="dim", no_wrap=True, overflow="ellipsis"),
            Text(f"File: {config_dir() / 'config.toml'}", style="dim", overflow="ellipsis", no_wrap=True),
        )

    def usage_tab(self) -> RenderableType:
        record = self._record()
        from claudepilot import sidebar

        # The forecast only looks back 24h; ~720 snapshots covers that at the default cadence.
        lines = sidebar.model_lines(record, state.read_limits(), state.read_usage_history(800), self.now)
        p = record.get("payload") or {}
        ctx = p.get("context_window") or {}
        extra = []
        if ctx.get("total_input_tokens") is not None:
            size = ctx.get("context_window_size") or 0
            extra.append(f"context {int(ctx['total_input_tokens']) // 1000}k"
                         + (f" of {int(size) // 1000}k tokens" if size else " tokens"))
        cost = (p.get("cost") or {}).get("total_cost_usd")
        if cost is not None:
            extra.append(f"session ${cost:.2f} at API rates (not billed on a plan)")
        return Group(*lines, *(Text(e, style="dim") for e in extra))

    def stats_tab(self) -> RenderableType:
        st = self._stats_cached()
        if st is None or not st.days:
            return Text("No history yet. Stats build up as you use Claude Code.", style="dim")
        today = time.strftime("%Y-%m-%d", time.localtime(self.now))
        prompts = sum(d.prompts for d in st.days)
        today_prompts = next((d.prompts for d in st.days if d.date == today), 0)
        mix: dict[str, int] = {}
        for d in st.days:
            for tier, n in d.mix.items():
                mix[tier] = mix.get(tier, 0) + n
        total = sum(mix.values()) or 1
        peaks5 = [d.peak_five_hour for d in st.days if d.peak_five_hour is not None]
        peaks7 = [d.peak_seven_day for d in st.days if d.peak_seven_day is not None]
        rate = f"{st.follow_rate * 100:.0f}%" if st.follow_rate is not None else "n/a"
        lv = st.token_levels
        rows = [
            ("Prompts", f"{today_prompts} today . {prompts} in 7 days"),
            ("Models", " ".join(f"{t} {mix.get(t, 0) * 100 // total}%"
                                for t in ("haiku", "sonnet", "opus"))),
            ("Peak", f"5h {max(peaks5):.0f}%" if peaks5 else "5h n/a"),
            ("", f"week {max(peaks7):.0f}%" if peaks7 else "week n/a"),
            ("Router", f"advice {st.switch_advice}, followed {rate}"),
            ("", f"auto {st.auto_ok} . deleg. {st.delegated} . kept {st.kept_for_cache}"),
            ("Tokens", f"lite {lv.get('lite', 0)} . caveman {lv.get('caveman', 0)}"),
            ("Guard", f"{sum(d.guard for d in st.days)} secret accesses caught"),
        ]
        return Group(_kv(rows), Text("7 days, local history. More: claudepilot stats", style="dim"))

    def render(self, console: Console) -> RenderableType:
        bar = Text()
        for i, name in enumerate(TABS):
            bar.append(f" {name} ", style="reverse bold" if i == self.tab else "dim")
        body = (self.status_tab, self.config_tab, self.usage_tab, self.stats_tab)[self.tab]()
        return frame(Group(bar, body), "USAGE", self.focused, "cyan", console.size.height,
                     "<- -> tabs . Esc: Claude")


# --------------------------------------------------------------------------- info

class _Background:
    """Housekeeping split between the status panels so nothing runs twice:
    CONTROL rings the alert bell and syncs history; GUIDE scans for unignored secrets."""

    def __init__(self, cwd: Path, alerts: bool, secrets: bool):
        self.cwd, self.alerts, self.scan = cwd, alerts, secrets
        self.secrets = unignored_secrets(cwd) if secrets else []
        self._last_scan = time.time()
        self._alerts_seen = self._alert_count() if alerts else 0
        self._last_sync = 0.0
        self.bell = bool(config.load()["alerts"]["bell"])

    @staticmethod
    def _alert_count() -> int:
        return sum(1 for e in state.read_events(200) if e.get("kind") == "alert")

    def tick(self, now: float) -> None:
        if self.scan and now - self._last_scan >= SECRET_SCAN_SECONDS:
            self.secrets, self._last_scan = unignored_secrets(self.cwd), now
        if not self.alerts:
            return
        count = self._alert_count()
        if count > self._alerts_seen and self.bell:
            print("\a", end="", flush=True)
        self._alerts_seen = count
        if now - self._last_sync >= HISTORY_SYNC_SECONDS:
            self._last_sync = now
            # Separate short-lived process: parsing a week of events would otherwise stay
            # resident in this long-running panel (Python rarely returns freed memory).
            with contextlib.suppress(OSError):
                subprocess.Popen([sys.executable, "-m", "claudepilot.hooks", "sync"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)


class StatusPanel:
    """CONTROL (part='control'), GUIDE (part='guide') or both stacked (part='info')."""

    focused = False

    def __init__(self, pane: str | None, cwd: Path, part: str = "info"):
        self.pane, self.cwd, self.part = pane, cwd, part
        self.now = time.time()
        self.bg = _Background(cwd, alerts=part in ("control", "info"), secrets=part in ("guide", "info"))
        self.rotate = float(config.load()["guide"]["rotate_seconds"])

    def tick(self, now: float) -> None:
        self.now = now
        self.bg.tick(now)

    def signature(self, now: float) -> tuple:
        buckets = (int(now // 30),                      # "data N min old", event timestamps
                   int(now // self.rotate) if self.part != "control" else 0,  # tip rotation
                   int(now // SECRET_SCAN_SECONDS) if self.bg.scan else 0,
                   int(now // HISTORY_SYNC_SECONDS) if self.bg.alerts else 0)
        return (self.focused, state.file_sig(*_state_files(self.pane)), buckets)

    def on_key(self, key: str) -> tui.Action:
        navigate(key, self.pane)
        return None

    def render(self, console: Console) -> RenderableType:
        from claudepilot import sidebar

        height = console.size.height
        reserve = 8 if self.part == "info" else 0  # rows taken by CONTROL in the combined view
        max_tips = max(1, (height - reserve - 2) // 3)
        control, guide = sidebar.control_and_guide(self.pane, self.now, self.bg.secrets, max_tips)
        hint = "Ctrl+arrows: panels . Esc: Claude"
        if self.part == "control":
            return frame(control.renderable, "CONTROL", self.focused, "magenta", height, hint)
        if self.part == "guide":
            return frame(guide.renderable, "GUIDE", self.focused, "yellow", height, hint)
        return Group(control, guide)


InfoPanel = StatusPanel  # backwards-compatible name


# --------------------------------------------------------------------------- control

SETTINGS = (  # (feature, label, values) in display order: 1 Router, 2 Tokens, 3 Guard
    ("router", "Router", ("off", "advise", "auto")),
    ("tokens", "Tokens", ("off", "auto")),
    ("guard", "Guard", ("off", "ask", "deny")),
)
VALUE_STYLE = {"off": "red", "advise": "green", "auto": "magenta", "ask": "yellow", "deny": "red"}


def explain(route: dict[str, Any], follow: dict[str, Any] | None) -> tuple[str, str]:
    """One-line, plain-language reason for a routing decision, and its style."""
    rec, cur = route.get("recommended"), route.get("current") or "?"
    action, reason, note = route.get("action"), route.get("reason") or "", route.get("note") or ""
    if action == "match":
        return f"on {cur}: {reason}", "dim"
    if action == "stay":
        ctx = note.split(" tokens", 1)[0] if "tokens" in note else "large"
        return f"kept {cur}, not {rec}: {ctx} ctx", "yellow"
    if action == "delegate":
        agent = "claudepilot-quick" if rec == "haiku" else "claudepilot-deep"
        return f"kept {cur}; suggested {agent}", "cyan"
    if action == "switch":
        status = (follow or {}).get("status")
        if status == "ok":
            return f"switched {cur} -> {rec} ({reason})", "green"
        if status == "unconfirmed":
            return f"switch to {rec} not confirmed", "red"
        if status == "skipped":
            return f"suggested {rec}: {(follow or {}).get('note', '')}", "yellow"
        if route.get("mode") == "advise":
            return f"suggested {rec} ({reason})", "green"
        return f"switching to {rec}...", "green"
    return reason or str(action), "dim"


class ControlPanel:
    """CONTROL: change Router / Tokens / Guard in place, and see why prompts were routed."""

    focused = False

    def __init__(self, pane: str | None, cwd: Path):
        self.pane = pane
        self.row = 0
        self.now = time.time()
        self.message = ""
        self.bg = _Background(cwd, alerts=True, secrets=False)

    def tick(self, now: float) -> None:
        self.now = now
        self.bg.tick(now)

    def signature(self, now: float) -> tuple:
        return (self.focused, self.row, self.message, state.file_sig(*_state_files(self.pane)),
                int(now // 30), int(now // HISTORY_SYNC_SECONDS))

    def values(self) -> list[str]:
        return [config.mode(feature) for feature, _, _ in SETTINGS]

    def change(self, step: int) -> None:
        feature, label, options = SETTINGS[self.row]
        current = config.mode(feature)
        value = options[(options.index(current) + step) % len(options)]
        config.set_mode(feature, value)
        self.message = f"{label}: {value}"

    def on_key(self, key: str) -> tui.Action:
        if key in ("up", "k"):
            self.row = (self.row - 1) % len(SETTINGS)
        elif key in ("down", "j"):
            self.row = (self.row + 1) % len(SETTINGS)
        elif key in ("1", "2", "3"):
            self.row = int(key) - 1
        elif key in ("right", "enter", "space", "l"):
            self.change(1)
        elif key in ("left", "h"):
            self.change(-1)
        else:
            navigate(key, self.pane)
        return None

    def render(self, console: Console) -> RenderableType:
        width = console.size.width - 4
        lines: list[RenderableType] = []
        events = state.read_events(limit=60, key=self.pane)
        level = next((e.get("level") for e in reversed(events) if e.get("kind") == "tokens"), None)
        for i, ((feature, label, options), value) in enumerate(zip(SETTINGS, self.values(), strict=True)):
            line = Text(no_wrap=True, overflow="ellipsis")
            line.append(f"{i + 1} {label:<7}", style="bold")
            for opt in options:
                if opt == value:
                    line.append(f"[{opt.upper()}]", style=f"bold {VALUE_STYLE.get(opt, 'white')}")
                else:
                    line.append(f" {opt} ", style="dim")
                line.append(" ")
            if feature == "tokens" and value == "auto" and level and level != "skip":
                line.append(f"({level})", style="dim")
            if self.focused and i == self.row:
                line.pad_right(max(0, width - line.cell_len))
                line.stylize("reverse")
            lines.append(line)
        if self.focused:
            lines.append(Text(self.message or "up/dn/1-3 pick . <-/-> change",
                              style="green" if self.message else "dim", no_wrap=True, overflow="ellipsis"))

        routes = [e for e in events if e.get("kind") == "route"]
        follows = [e for e in events if e.get("kind") == "auto_route"]
        room = max(1, console.size.height - len(lines) - 3)
        if routes:
            lines.append(Text("Last prompts", style="bold"))
            for route in routes[-min(room - 1, 4):][::-1]:
                follow = next((f for f in follows if 0 <= f["ts"] - route["ts"] <= 60), None)
                text, style = explain(route, follow)
                ts = time.strftime("%H:%M", time.localtime(route["ts"]))
                lines.append(Text(f"{ts} {text}", style=style, no_wrap=True, overflow="ellipsis"))
        else:
            lines.append(Text("No prompts routed in this pane yet.", style="dim"))
        last_guard = next((e for e in reversed(events) if e.get("kind") == "guard"), None)
        if last_guard:
            ts = time.strftime("%H:%M", time.localtime(last_guard["ts"]))
            lines.append(Text(f"{ts} guard {last_guard.get('decision')}: {last_guard.get('file')}",
                              style="red", no_wrap=True, overflow="ellipsis"))
        return frame(Group(*lines), "CONTROL", self.focused, "magenta", console.size.height,
                     "Esc: Claude")


# --------------------------------------------------------------------------- files

def editor_command(configured: str | None = None) -> list[str] | None:
    for candidate in (configured, os.environ.get("VISUAL"), os.environ.get("EDITOR"), "vim", "vi", "nano"):
        if candidate:
            parts = shlex.split(candidate)
            if parts and shutil.which(parts[0]):
                return parts
    return None


def _tmux_client_attached() -> bool:
    pane = os.environ.get("TMUX_PANE")
    if not pane or not shutil.which("tmux"):
        return False
    try:
        out = subprocess.run(["tmux", "display-message", "-p", "-t", pane, "#{session_attached}"],
                             capture_output=True, text=True, timeout=3).stdout.strip()
        return out.isdigit() and int(out) > 0
    except (OSError, subprocess.SubprocessError):
        return False


class FilesPanel:
    focused = False
    COLORS = {"M": "yellow", "A": "green", "D": "red", "?": "grey50", "R": "cyan", "*": "yellow"}

    def __init__(self, cwd: Path, claude_pane: str | None = None):
        self.cwd, self.claude_pane = cwd, claude_pane
        self.tree = filetree.FileTree()
        self.git = GitStatus(is_repo=False)
        self.message = ""
        self._sig = -1.0
        self._last_status = self._last_list = 0.0
        self.version = 0
        self.page = 10
        self.editor = config.load()["files"]["editor"]
        self._files: list[str] = []
        self.refresh(time.time())

    def refresh(self, now: float, relist: bool = True) -> None:
        """git status always; the full file list only when asked (it is the costly part)."""
        old = (self.tree.rows, self.git.changes, self.git.branch)
        self.git = read_status(self.cwd)
        statuses = {path: code for code, path in self.git.changes}
        files = filetree.list_files(self.cwd) if relist else None
        if files is None:
            files = self._files
        self._files = files
        self.tree.update(files, statuses)
        self._sig = git_dir_mtime(self.cwd)
        self._last_status = now
        if relist:
            self._last_list = now
        if (self.tree.rows, self.git.changes, self.git.branch) != old:
            self.version += 1

    def tick(self, now: float) -> None:
        index_changed = git_dir_mtime(self.cwd) != self._sig
        if index_changed or now - self._last_list >= FILE_LIST_SECONDS:
            self.refresh(now, relist=True)
        elif now - self._last_status >= GIT_STATUS_SECONDS:
            self.refresh(now, relist=False)

    def signature(self, now: float) -> tuple:
        # git_dir_mtime is two stat() calls; the time bucket drives the periodic refresh.
        return (self.focused, self.version, self.tree.cursor_path, self.tree.offset, self.message,
                git_dir_mtime(self.cwd), int(now // GIT_STATUS_SECONDS))

    def open_current(self) -> tui.Action:
        row = self.tree.current
        if not row:
            return None
        if row.is_dir:
            self.tree.toggle()
            return None
        cmd = editor_command(self.editor)
        if not cmd:
            self.message = "No editor found. Set [files] editor in config.toml."
            return None
        argv = [*cmd, "--", row.path] if cmd[0] in ("vim", "vi", "nvim") else [*cmd, row.path]
        self.message = ""
        if _tmux_client_attached():
            # A large popup over the whole window: the 42-column pane is too narrow to edit in.
            subprocess.run(["tmux", "display-popup", "-E", "-w", "90%", "-h", "90%", "-d", str(self.cwd),
                            "-T", f" {row.path} ", " ".join(shlex.quote(a) for a in argv)], check=False)
            self.refresh(time.time())
            return None

        def run_in_pane() -> None:
            subprocess.run(argv, cwd=self.cwd, check=False)
            self.refresh(time.time())

        return run_in_pane

    def on_key(self, key: str) -> tui.Action:
        t = self.tree
        if key == "escape":
            navigate("escape", self.claude_pane)
        elif key in ("up", "k"):
            t.move(-1)
        elif key in ("down", "j"):
            t.move(1)
        elif key == "pageup":
            t.move(-self.page)
        elif key in ("pagedown", "space"):
            t.move(self.page)
        elif key in ("home", "g"):
            t.move_to(0)
        elif key in ("end", "G"):
            t.move_to(len(t.rows) - 1)
        elif key == "right":
            t.toggle(True)
        elif key == "left":
            t.collapse_or_parent()
        elif key == "enter":
            return self.open_current()
        elif key == "r":
            self.refresh(time.time())
        return None

    def render(self, console: Console) -> RenderableType:
        width, height = console.size.width - 4, console.size.height
        head = Text(overflow="ellipsis", no_wrap=True)
        if self.git.is_repo:
            c = self.git.counts
            head.append(self.git.branch or "?", style="bold")
            if self.git.ahead:
                head.append(f" +{self.git.ahead}", style="green")
            if self.git.behind:
                head.append(f" -{self.git.behind}", style="red")
            head.append(f"  M{c['M']} A{c['A']} D{c['D']} ?{c['?']}", style="dim")
        else:
            head.append("not a git repo", style="dim")
        body_height = max(1, height - 3 - (1 if self.message else 0))
        self.page = max(1, body_height - 1)
        rows, cur = self.tree.window(body_height)
        lines: list[RenderableType] = [head]
        for i, row in enumerate(rows):
            marker = ("- " if row.expanded else "+ ") if row.is_dir else "  "
            name = row.name + ("/" if row.is_dir else "")
            code = (row.status[:1] or " ") + " "
            text = Text(no_wrap=True, overflow="ellipsis")
            text.append(code, style=self.COLORS.get(row.status[:1], ""))
            text.append("  " * row.depth + marker + name, style="bold blue" if row.is_dir else "")
            if i == cur:
                text.pad_right(max(0, width - text.cell_len))
                text.stylize("reverse")
            lines.append(text)
        if not rows:
            lines.append(Text("(no files)", style="dim"))
        if self.message:
            lines.append(Text(self.message, style="red"))
        total = len(self.tree.rows)
        pos = f"{self.tree.cursor + 1}/{total}" if total else "0/0"
        return frame(Group(*lines), f"FILES  {self.cwd.name}/", self.focused, "blue", height,
                     f"{pos} . Enter: open")


def run(kind: str, pane: str | None, cwd: Path) -> None:
    refresh = float(config.load()["workspace"]["refresh_seconds"])
    panel = {
        "usage": lambda: UsagePanel(pane),
        "control": lambda: ControlPanel(pane, cwd),
        "guide": lambda: StatusPanel(pane, cwd, "guide"),
        "info": lambda: StatusPanel(pane, cwd, "info"),
        "files": lambda: FilesPanel(cwd, pane),
    }[kind]()
    tui.run(panel, refresh)
