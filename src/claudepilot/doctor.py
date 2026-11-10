"""`claudepilot doctor`: check that every integration point is wired up."""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass

from claudepilot import config, install, state
from claudepilot.paths import claude_settings_path, config_dir
from claudepilot.router.classifier import validate_file

MIN_CLAUDE = (2, 1, 251)  # PostModelSwitch hook + rate_limits in statusline


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    warn_only: bool = False


def _version(cmd: list[str]) -> str | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None


def run_checks() -> list[Check]:
    checks: list[Check] = []

    tmux = shutil.which("tmux")
    checks.append(Check("tmux", bool(tmux), _version(["tmux", "-V"]) or "not found: install tmux"))

    claude_cmd = config.load()["workspace"]["claude_command"].split()[0]
    claude = shutil.which(claude_cmd)
    if claude:
        out = _version([claude, "--version"]) or ""
        m = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
        ver = tuple(int(x) for x in m.groups()) if m else None
        ok = ver is not None and ver >= MIN_CLAUDE
        need = ".".join(map(str, MIN_CLAUDE))
        checks.append(Check("claude", ok, f"{out or 'unknown version'}"
                            + ("" if ok else f" (need ≥ {need} for model-switch events)"),
                            warn_only=True))
    else:
        checks.append(Check("claude", False, f"`{claude_cmd}` not found on PATH"))

    path = claude_settings_path()
    try:
        meta = state.read_json(state.state_dir() / "install.json")
        settings = install.load_settings(path)
        if install.plugin_enabled(settings):
            dupes = [k for k, v in install.is_installed(settings).items() if v and k != "statusLine"]
            checks.append(Check("plugin", not dupes,
                                "enabled" + (f"; hooks also in settings.json ({', '.join(dupes)}), so "
                                             "they run twice. Run `claudepilot install` to fix"
                                             if dupes else "; provides hooks + subagents")))
            meta = {**meta, "hooks": False}
        status = install.is_installed(settings, meta.get("hooks", True))
        missing = [k for k, v in status.items() if not v]
        checks.append(Check("settings.json", not missing,
                            f"{path}" + (f": missing {', '.join(missing)}; run `claudepilot install`"
                                         if missing else ": " + ("statusline + hooks registered"
                                                                 if meta.get("hooks", True)
                                                                 else "statusline only (no hooks)"))))
    except (ValueError, OSError) as exc:
        checks.append(Check("settings.json", False, f"{path}: cannot parse ({exc})"))

    record = state.read_json(state.state_dir() / "latest.json")
    if record:
        age = time.time() - record.get("received_at", 0)
        has_limits = bool((record.get("payload") or {}).get("rate_limits"))
        checks.append(Check("statusline data", True, f"last update {int(age)}s ago"
                            + ("" if has_limits else "; no rate_limits yet (Pro/Max, after 1st reply)")))
    else:
        checks.append(Check("statusline data", False,
                            "nothing received yet: start Claude Code and send a prompt",
                            warn_only=True))

    user_rules = config_dir() / "models.yaml"
    if user_rules.exists():
        problems = validate_file(user_rules)
        checks.append(Check("router rules", not problems, "; ".join(problems) or str(user_rules)))
    checks.append(Check("subagents", install.agents_available() or not meta_hooks(),
                        "claudepilot-quick + claudepilot-deep available" if install.agents_available()
                        else "not installed: run `claudepilot install`", warn_only=True))
    checks.append(Check("modes", True, f"router {config.router_mode()}, tokens {config.token_mode()}, "
                                       f"guard {config.guard_mode()}"))
    return checks


def meta_hooks() -> bool:
    return bool(state.read_json(state.state_dir() / "install.json").get("hooks", True))
