"""ClaudePilot command-line interface."""

from __future__ import annotations

import contextlib
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from claudepilot import __version__, config, state, workspace

app = typer.Typer(
    help="✈ ClaudePilot: a tmux cockpit for Claude Code.",
    no_args_is_help=False,
    add_completion=True,
    rich_markup_mode="rich",
)
console = Console()


def _version(value: bool) -> None:
    if value:
        console.print(f"claudepilot {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", callback=_version, is_eager=True),
) -> None:
    """Run with no command to launch (or reattach to) the cockpit for this directory."""
    if ctx.invoked_subcommand is None:
        start(attach=True)


@app.command()
def start(attach: bool = typer.Option(True, help="Attach after creating.")) -> None:
    """Create the cockpit session for the current directory (reuses an existing one)."""
    cfg = config.load()["workspace"]
    cwd = Path.cwd()
    try:
        name = workspace.session_name(cwd)
        existed = workspace.exists(name)
        workspace.create(cwd, cfg["claude_command"], int(cfg["sidebar_width"]),
                         close_with_claude=bool(cfg["close_with_claude"]),
                         nav_keys=bool(cfg["nav_keys"]), mouse=bool(cfg["mouse"]))
        if not existed:
            _hint_install()
        if attach:
            workspace.attach(name)
        else:
            console.print(f"[green]✓[/] session [bold]{name}[/] ready. `claudepilot attach`")
    except workspace.TmuxError as exc:
        console.print(f"[red]✗ {exc}[/]")
        raise typer.Exit(1) from exc


@app.command("attach")
def attach_cmd() -> None:
    """Reattach to this directory's cockpit."""
    name = workspace.session_name(Path.cwd())
    if not workspace.exists(name):
        console.print("No cockpit for this directory. Run `claudepilot`.")
        raise typer.Exit(1)
    workspace.attach(name)


@app.command()
def stop() -> None:
    """Stop this directory's cockpit (ends the Claude Code session in it)."""
    name = workspace.session_name(Path.cwd())
    if workspace.stop(name):
        console.print(f"[green]✓[/] stopped {name}")
    else:
        console.print("No cockpit running for this directory.")


@app.command()
def sidebar(
    pane: str = typer.Option(None, help="tmux pane id of the Claude pane"),
    cwd: Path = typer.Option(Path.cwd, help="project directory"),
    once: bool = typer.Option(False, help="render once and exit"),
) -> None:
    """Run the sidebar UI (started automatically inside the cockpit)."""
    from claudepilot import sidebar as sb

    with contextlib.suppress(KeyboardInterrupt):
        sb.run(pane, cwd, once=once)


@app.command()
def panel(
    kind: str = typer.Argument(..., help="usage | control | guide | files | info (control+guide)"),
    pane: str = typer.Option(None, help="tmux pane id of the Claude pane"),
    cwd: Path = typer.Option(Path.cwd, help="project directory"),
) -> None:
    """Run one interactive cockpit panel (started automatically inside the cockpit)."""
    from claudepilot import panels

    if kind not in {"usage", "control", "guide", "info", "files"}:
        console.print("[red]panel must be usage, control, guide, files or info[/]")
        raise typer.Exit(2)
    with contextlib.suppress(KeyboardInterrupt):
        panels.run(kind, pane, cwd)


@app.command()
def install(
    statusline_only: bool = typer.Option(
        False, "--statusline-only",
        help="Only the statusline (usage, model, forecast): no router, token mode or guard hooks."),
    agents: bool = typer.Option(True, help="Install the claudepilot-quick/-deep subagents."),
) -> None:
    """Register the statusline + hooks in ~/.claude/settings.json (with backup)."""
    from claudepilot import install as inst
    from claudepilot.paths import claude_settings_path

    try:
        if not statusline_only and inst.plugin_enabled(inst.load_settings(claude_settings_path())):
            console.print("[yellow]![/] The ClaudePilot plugin is enabled and provides the hooks and "
                          "subagents, so only the statusline is installed.")
            statusline_only = True
        path, bak, chained = inst.install(hooks=not statusline_only)
    except ValueError as exc:
        console.print(f"[red]✗ settings.json is not valid JSON, refusing to edit: {exc}[/]")
        raise typer.Exit(1) from exc
    what = "statusline only" if statusline_only else "statusline + prompt, model-switch and guard hooks"
    console.print(f"[green]✓[/] registered {what} in {path}")
    if statusline_only:
        console.print("  Tip: set [bold][statusline] style = \"full\"[/] for bars + forecast without tmux.")
    if bak:
        console.print(f"  backup: {bak}")
    if agents and not statusline_only:
        written = inst.install_agents()
        if written:
            console.print(f"  subagents: {', '.join(written)} in {inst.agents_dir()}")
    if chained:
        console.print(f"  your existing statusline is preserved and chained: [dim]{chained}[/]")
    console.print("  Restart running Claude Code sessions to pick up the change.")


@app.command()
def uninstall() -> None:
    """Remove ClaudePilot from settings.json and restore your previous statusline."""
    from claudepilot import install as inst

    path, bak = inst.uninstall()
    removed = inst.uninstall_agents()
    console.print(f"[green]✓[/] removed ClaudePilot entries from {path}")
    if removed:
        console.print(f"  removed subagents: {', '.join(removed)}")
    if bak:
        console.print(f"  backup: {bak}")


@app.command()
def doctor() -> None:
    """Check tmux, Claude Code, hooks registration and incoming data."""
    from claudepilot import doctor as doc

    failed = False
    for check in doc.run_checks():
        icon = "[green]✓[/]" if check.ok else ("[yellow]![/]" if check.warn_only else "[red]✗[/]")
        failed |= not check.ok and not check.warn_only
        console.print(f"{icon} [bold]{check.name}[/]: {check.detail}")
    raise typer.Exit(1 if failed else 0)


@app.command()
def usage() -> None:
    """One-shot usage report (reported limits + estimated forecast)."""
    from claudepilot.usage.forecast import forecast
    from claudepilot.usage.limits import effective_windows, format_countdown

    record = state.read_json(state.state_dir() / "latest.json")
    payload = record.get("payload") or {}
    now = time.time()
    wins = effective_windows(payload, state.read_limits(), now)
    if not wins:
        console.print("No reported limits yet. They appear for Pro/Max after the first reply "
                      "(or after /usage) in Claude Code.")
        raise typer.Exit(0)
    table = Table(title="Claude usage", caption="used/resets: reported by Claude Code · forecast: estimate")
    for col in ("window", "used", "resets in", "forecast", "reported"):
        table.add_column(col)
    history = state.read_usage_history()
    for name, win in wins.items():
        reported = "live" if win.live else f"{format_countdown(win.cached_age or 0)} ago"
        if win.reset_since:
            table.add_row(win.label, "reset", "?", "n/a", reported)
            continue
        fc = forecast(history, name, win.used_percentage, win.resets_at, now)
        if fc is None:
            fc_text = "n/a"
        elif fc.hits_at:
            fc_text = f"limit ~{time.strftime('%a %H:%M', time.localtime(fc.hits_at))}"
        else:
            fc_text = "resets first"
        table.add_row(win.label, f"{win.used_percentage:.0f}%",
                      format_countdown(win.resets_at - now) if win.resets_at else "?", fc_text, reported)
    console.print(table)


@app.command()
def model() -> None:
    """Show the current model and router mode."""
    record = state.read_json(state.state_dir() / "latest.json")
    m = (record.get("payload") or {}).get("model") or {}
    console.print(f"model:  [bold cyan]{m.get('display_name', 'unknown')}[/] [dim]{m.get('id', '')}[/]")
    console.print(f"router: [bold]{config.router_mode()}[/]")
    console.print(f"tokens: [bold]{config.token_mode()}[/]")
    console.print(f"guard:  [bold]{config.guard_mode()}[/]")


@app.command()
def router(mode: str = typer.Argument(..., help="off | advise | auto (experimental)")) -> None:
    """Set the prompt router mode."""
    if mode not in {"off", "advise", "auto"}:
        console.print("[red]mode must be off, advise or auto[/]")
        raise typer.Exit(2)
    config.set_mode("router", mode)
    console.print(f"[green]✓[/] router mode: [bold]{mode}[/]")
    if mode == "auto":
        console.print("[yellow]auto (the default) is experimental: it blocks a prompt, sends /model, "
                      "waits for Claude Code to confirm, then resubmits. Requires the cockpit (tmux).[/]")


@app.command()
def tokens(mode: str = typer.Argument(..., help="off | auto")) -> None:
    """Set token optimization: auto adds a brevity instruction to each prompt."""
    if mode not in {"off", "auto"}:
        console.print("[red]mode must be off or auto[/]")
        raise typer.Exit(2)
    config.set_mode("tokens", mode)
    console.print(f"[green]✓[/] token optimization: [bold]{mode}[/]")
    if mode == "auto":
        console.print("  lite: concise answers. caveman: ultra-terse, once usage or context is high.")
        console.print("  Code, commands, paths and errors are never shortened. 'In detail' asks are skipped.")


@app.command()
def guard(mode: str = typer.Argument(..., help="off | ask | deny")) -> None:
    """Set the secret guard: ask (confirm) or deny when Claude touches secret-looking files."""
    if mode not in {"off", "ask", "deny"}:
        console.print("[red]mode must be off, ask or deny[/]")
        raise typer.Exit(2)
    config.set_mode("guard", mode)
    console.print(f"[green]✓[/] secret guard: [bold]{mode}[/]")
    _hint_install()


@app.command()
def wrapped(
    days: int = typer.Option(7, help="how many days to summarise"),
    plain: bool = typer.Option(False, "--plain", help="plain text, for pasting"),
) -> None:
    """Your Claude Code week as a shareable card (local data only, no prompt text)."""
    from rich.panel import Panel
    from rich.text import Text

    from claudepilot import wrapped as wr
    from claudepilot.storage import db

    now = time.time()
    db.sync()
    since = now - days * 86_400
    card = wr.compute(db.load_events(since), db.load_usage(since), now, days=days)
    rows = wr.lines(card)
    if plain:
        for text, _ in rows:
            print(text)
        return
    body = Text()
    for i, (text, style) in enumerate(rows):
        body.append(text, style=style or None)
        if i < len(rows) - 1:
            body.append("\n")
    console.print(Panel(body, title="ClaudePilot Wrapped", subtitle="local data only",
                        border_style="magenta", width=64, padding=(1, 2)))


@app.command()
def stats(days: int = typer.Option(30, help="how many days to analyse")) -> None:
    """Routing and usage analytics from local history (SQLite)."""
    from claudepilot.storage import db

    st = db.stats(days)
    table = Table(title=f"Last {days} days", caption="usage peaks: reported · everything else: local events")
    for col in ("day", "prompts", "haiku/sonnet/opus", "peak 5h", "peak week", "guard"):
        table.add_column(col, justify="right" if col != "day" else "left")
    for d in st.days[-14:]:
        mix = "/".join(str(d.mix.get(t, 0)) for t in ("haiku", "sonnet", "opus"))
        pct = lambda v: f"{v:.0f}%" if v is not None else "-"  # noqa: E731
        table.add_row(d.date, str(d.prompts), mix, pct(d.peak_five_hour), pct(d.peak_seven_day),
                      str(d.guard or ""))
    console.print(table)
    rate = f"{st.follow_rate * 100:.0f}%" if st.follow_rate is not None else "n/a"
    console.print(f"[bold]Router[/]  switch advice {st.switch_advice} (followed {st.followed}, {rate}) · "
                  f"delegated {st.delegated} · kept for cache {st.kept_for_cache}")
    console.print(f"        escalations {st.escalations} · follow-ups kept on model {st.followups} · "
                  f"auto-switch ok {st.auto_ok}, unconfirmed {st.auto_failed}")
    if st.token_levels:
        lv = st.token_levels
        console.print(f"[bold]Tokens[/]  lite {lv.get('lite', 0)} · caveman {lv.get('caveman', 0)} · "
                      f"skipped {lv.get('skip', 0)}")


@app.command()
def statusline() -> None:
    """Statusline entrypoint (same as the hook). Pipe Claude Code statusline JSON in to test."""
    import json
    import sys

    from claudepilot.hooks import statusline as sl

    try:
        payload = json.load(sys.stdin)
    except ValueError:
        payload = {}
    print(sl.run(payload if isinstance(payload, dict) else {}))


@app.command()
def classify(prompt: str) -> None:
    """Show how the router would classify a prompt (nothing is stored)."""
    from claudepilot.router.classifier import classify as cls

    d = cls(prompt)
    console.print(f"[bold]{d.label}[/] ({d.alias}): {d.reason} "
                  f"[dim](rule: {d.rule}, {d.word_count} words)[/]")


@app.command()
def reset(history: bool = typer.Option(False, "--history", help="delete usage + event history")) -> None:
    """Delete locally stored ClaudePilot data."""
    if not history:
        console.print("Pass --history to delete usage snapshots and router events.")
        raise typer.Exit(0)
    from claudepilot.storage import db

    for name in ("usage_history.jsonl", "events.jsonl", "alerts.json"):
        (state.state_dir() / name).unlink(missing_ok=True)
    db.delete()
    console.print("[green]✓[/] history deleted")


def _hint_install() -> None:
    from claudepilot import install as inst
    from claudepilot.paths import claude_settings_path

    try:
        meta = state.read_json(state.state_dir() / "install.json")
        status = inst.is_installed(inst.load_settings(claude_settings_path()), meta.get("hooks", True))
    except (ValueError, OSError):
        return
    if not all(status.values()):
        console.print("[yellow]![/] Hooks are not registered: run `claudepilot install` "
                      "so the sidebar gets live data.")
