"""The cockpit sidebar: model & limits, router activity, project status (Rich Live)."""

from __future__ import annotations

import contextlib
import sys
import time
from pathlib import Path
from typing import Any

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from claudepilot import config, guide, state, tokens
from claudepilot.explorer.git import GitStatus, git_dir_mtime, read_status, unignored_secrets
from claudepilot.paths import safe_key
from claudepilot.router.classifier import tier_of_model
from claudepilot.usage.forecast import forecast
from claudepilot.usage.limits import effective_windows, format_countdown

GIT_REFRESH_SECONDS = 3.0
HISTORY_SYNC_SECONDS = 300.0
STALE_SECONDS = 15 * 60


def bar(pct: float, width: int = 12) -> Text:
    pct = max(0.0, min(100.0, pct))
    filled = round(pct / 100 * width)
    color = "green" if pct < 60 else "yellow" if pct < 85 else "red"
    return Text("█" * filled, style=color) + Text("░" * (width - filled), style="grey37")


def model_panel(record: dict[str, Any], cache: dict[str, Any], history: list[dict[str, Any]],
                now: float) -> Panel:
    return Panel(Group(*model_lines(record, cache, history, now)), title="MODEL & LIMITS",
                 border_style="cyan", title_align="left")


def model_lines(record: dict[str, Any], cache: dict[str, Any], history: list[dict[str, Any]],
                now: float) -> list[RenderableType]:
    payload = record.get("payload") or {}
    model = payload.get("model") or {}
    rows = Table.grid(padding=(0, 1))
    rows.add_column(width=5)
    rows.add_column()
    rows.add_column(justify="right")
    lines: list[RenderableType] = []

    if payload:
        lines.append(Text.assemble(
            (model.get("display_name") or "unknown", "bold cyan"),
            ("  " + (model.get("id") or ""), "dim"),
        ))
    else:
        # Never show another session's model here: wait for this pane's own data.
        lines.append(Text("Model: waiting for Claude Code...", style="dim"))

    # Limits are account-wide: show the last reported values right away, even before
    # this session's first reply.
    wins = effective_windows(payload, cache, now)
    for win in wins.values():
        if win.reset_since:
            rows.add_row(win.label, Text("reset since last report", style="green"), "")
            continue
        countdown = format_countdown(win.resets_at - now) if win.resets_at else ""
        rows.add_row(win.label, bar(win.used_percentage) + Text(f" {win.used_percentage:3.0f}%"),
                     Text(countdown, style="dim"))
    ctx = (payload.get("context_window") or {}).get("used_percentage")
    if ctx is not None:
        rows.add_row("ctx", bar(float(ctx)) + Text(f" {float(ctx):3.0f}%"), "")
    if rows.row_count:
        lines.append(rows)

    if not wins:
        lines.append(Text("Usage appears after the first reply\n(Pro/Max), or run /usage.", style="dim"))
    else:
        ages = [w.cached_age for w in wins.values() if w.cached_age is not None]
        if ages and max(ages) >= 60:
            lines.append(Text(f"usage last reported {format_countdown(max(ages))} ago", style="dim"))

    for name in ("five_hour", "seven_day"):
        win = wins.get(name)
        if not win or win.reset_since:
            continue
        fc = forecast(history, name, win.used_percentage, win.resets_at, now)
        if fc and fc.hits_at:
            when = time.strftime("%H:%M" if name == "five_hour" else "%a %H:%M",
                                 time.localtime(fc.hits_at))
            style = "bold red" if fc.hits_at - now < 3600 else "yellow"
            lines.append(Text(f"! At this pace: {win.label} limit ~{when} (est.)", style=style))
        elif fc and fc.resets_first and name == "five_hour":
            lines.append(Text(f"On pace to reset before {win.label} limit (est.)", style="green"))

    received = record.get("received_at", 0)
    if received and now - received > STALE_SECONDS:
        lines.append(Text(f"session data {format_countdown(now - received)} old", style="dim"))
    return lines


MODE_STYLE = {"off": "dim", "advise": "green", "auto": "magenta"}
TOKEN_LEVEL_STYLE = {"lite": "green", "caveman": "yellow", "skip": "dim"}


def control_panel(events: list[dict[str, Any]], router_mode: str, token_mode: str,
                  guard_mode: str = "ask") -> Panel:
    """Router + token-optimization status, plus the latest routing activity."""
    last_tokens = next((e for e in reversed(events) if e.get("kind") == "tokens"), None)
    level = (last_tokens or {}).get("level")
    modes = Text.assemble(
        ("Router ", "bold"), (router_mode.upper(), MODE_STYLE.get(router_mode, "white")),
        ("   Tokens ", "bold"), (token_mode.upper(), MODE_STYLE.get(token_mode, "white")),
    )
    if token_mode == "auto" and level:
        modes.append(f" ({level})", style=TOKEN_LEVEL_STYLE.get(level, "white"))
    lines: list[RenderableType] = [modes]
    guard_line = Text.assemble(("Guard ", "bold"),
                               (guard_mode.upper(), "dim" if guard_mode == "off" else "red"))
    last_guard = next((e for e in reversed(events) if e.get("kind") == "guard"), None)
    if last_guard:
        ts = time.strftime("%H:%M", time.localtime(last_guard.get("ts", 0)))
        guard_line.append(f"  {ts} {last_guard.get('decision')} {last_guard.get('file')}", style="yellow")
    lines.append(guard_line)

    routes = [e for e in events if e.get("kind") in ("route", "auto_route", "model_switch")]
    for ev in routes[-3:]:
        ts = time.strftime("%H:%M", time.localtime(ev.get("ts", 0)))
        if ev["kind"] == "route":
            icon = {"match": "ok", "switch": "->", "stay": "="}.get(ev.get("action"), "-")
            text = f"{ts} {icon} {ev.get('reason')} -> {ev.get('recommended')}"
            if ev.get("action") == "stay":
                text += " (stay: cache)"
        elif ev["kind"] == "auto_route":
            text = f"{ts} auto -> {ev.get('to')}: {ev.get('status')}"
        else:
            text = f"{ts} model -> {ev.get('to_model')}"
        style = "" if ev is routes[-1] else "dim"
        lines.append(Text(text, overflow="ellipsis", no_wrap=True, style=style))
    for ev in [e for e in events if e.get("kind") == "alert"][-2:]:
        lines.append(Text(f"! {ev.get('message')}", style="yellow"))
    if not routes:
        lines.append(Text("No prompts routed yet.", style="dim"))
    return Panel(Group(*lines), title="CONTROL", border_style=MODE_STYLE.get(router_mode, "white"),
                 title_align="left")


GUIDE_STYLE = {"security": "red", "policy": "blue", "tokens": "green", "model": "cyan"}


def guide_panel(tips: list[guide.Tip]) -> Panel:
    lines: list[RenderableType] = []
    for tip in tips:
        style = GUIDE_STYLE.get(tip.category, "white")
        lines.append(Text.assemble(
            (("! " if tip.urgent else "") + f"{tip.label} ", f"bold {style}"),
            (tip.text, "" if tip.urgent else "default"),
        ))
    if not lines:
        lines.append(Text("No tips.", style="dim"))
    return Panel(Group(*lines), title="GUIDE", border_style="yellow", title_align="left")


def git_panel(status: GitStatus, cwd: Path, max_files: int = 8) -> Panel:
    if not status.is_repo:
        return Panel(Text(f"{cwd.name}/ (not a git repo)", style="dim"),
                     title="PROJECT", border_style="blue")
    head = Text.assemble(("branch ", "blue"), (status.branch or "?", "bold"))
    if status.ahead:
        head.append(f" ↑{status.ahead}", style="green")
    if status.behind:
        head.append(f" ↓{status.behind}", style="red")
    c = status.counts
    summary = Text(f"M {c['M']}  A {c['A']}  D {c['D']}  ? {c['?']}", style="dim")
    lines: list[RenderableType] = [head, summary]
    colors = {"M": "yellow", "A": "green", "D": "red", "?": "grey50"}
    for code, path in status.changes[:max_files]:
        lines.append(Text.assemble((f"{code:<2} ", colors.get(code[0], "white")),
                                   (path, ""), overflow="ellipsis", no_wrap=True))
    if len(status.changes) > max_files:
        lines.append(Text(f"… +{len(status.changes) - max_files} more", style="dim"))
    if status.last_commit:
        lines.append(Text(status.last_commit, style="dim", overflow="ellipsis", no_wrap=True))
    return Panel(Group(*lines), title=f"PROJECT  {cwd.name}/", border_style="blue",
                 title_align="left")


def control_and_guide(pane: str | None, now: float, secrets: list[str] | None = None,
                      max_tips: int | None = None) -> list[RenderableType]:
    record = state.read_session_state(pane, fallback=pane is None)
    payload = record.get("payload") or {}
    events = state.read_events(limit=60, key=pane)
    cfg = config.load()
    usage, _ = tokens.pressure(payload, state.read_limits(), now)
    ctx = (payload.get("context_window") or {}).get("used_percentage")
    model = payload.get("model") or {}
    meta = state.read_json(state.state_dir() / "panes" / f"{safe_key(pane)}.meta.json") if pane else {}
    count = int(cfg["guide"]["tips"])
    tips = guide.pick(
        guide.contextual_tips(
            ctx_pct=float(ctx) if ctx is not None else None,
            usage_pct=usage,
            model_tier=tier_of_model(model.get("id")) or tier_of_model(model.get("display_name")),
            events=events,
            permission_mode=meta.get("permission_mode"),
            unignored_secrets=secrets or [],
        ),
        now,
        count=count if max_tips is None else max(1, min(count, max_tips)),
        rotate_seconds=float(cfg["guide"]["rotate_seconds"]),
    )
    return [
        control_panel(events, config.router_mode(), config.token_mode(), config.guard_mode()),
        guide_panel(tips),
    ]


def render(pane: str | None, cwd: Path, git: GitStatus, now: float,
           secrets: list[str] | None = None) -> RenderableType:
    """All-in-one view (`claudepilot sidebar --once`). The cockpit uses panels.py instead."""
    # With a pane, show only that pane's session; without one, the most recent session.
    record = state.read_session_state(pane, fallback=pane is None)
    return Group(
        model_panel(record, state.read_limits(), state.read_usage_history(2000), now),
        *control_and_guide(pane, now, secrets),
        git_panel(git, cwd),
    )


def run(pane: str | None, cwd: Path, once: bool = False) -> None:
    cfg = config.load()
    console = Console()
    refresh = float(cfg["workspace"]["refresh_seconds"])
    git = read_status(cwd)
    secrets = unignored_secrets(cwd)
    if once:
        console.print(render(pane, cwd, git, time.time(), secrets))
        return

    last_git_check = time.time()
    last_git_sig = git_dir_mtime(cwd)
    alerts_seen = sum(1 for e in state.read_events(200) if e.get("kind") == "alert")
    last_sync = 0.0
    with Live(render(pane, cwd, git, time.time(), secrets), console=console, screen=True,
              auto_refresh=False) as live:
        while True:
            time.sleep(refresh)
            now = time.time()
            sig = git_dir_mtime(cwd)
            # Re-run git when the index changes, or every few seconds for worktree edits.
            if sig != last_git_sig or now - last_git_check >= GIT_REFRESH_SECONDS:
                git, last_git_sig, last_git_check = read_status(cwd), sig, now
                secrets = unignored_secrets(cwd)
            alert_count = sum(1 for e in state.read_events(200) if e.get("kind") == "alert")
            if alert_count > alerts_seen and cfg["alerts"]["bell"]:
                sys.stdout.write("\a")
                sys.stdout.flush()
            alerts_seen = alert_count
            if now - last_sync >= HISTORY_SYNC_SECONDS:
                last_sync = now
                from claudepilot.storage import db

                with contextlib.suppress(Exception):  # history is best-effort; never kill the UI
                    db.sync()
            live.update(render(pane, cwd, git, now, secrets), refresh=True)
