"""UserPromptSubmit hook: classify the prompt and advise on (or, experimentally, apply) a model.

Privacy: the prompt text never touches disk. Only its category, word count and a
SHA-256 hash (the auto-mode loop guard) are recorded. In auto mode the text is held
briefly in a tmux paste buffer (in memory) so that it can be resubmitted.

Hooks cannot change the session model. Auto mode therefore:
  1. blocks this prompt,
  2. stashes it in a tmux buffer,
  3. spawns a detached `resubmit` helper, which types `/model <alias>`, waits until
     Claude Code CONFIRMS the switch (PostModelSwitch event or statusline), then
     pastes the prompt back and submits it.
If confirmation never arrives, the prompt is pasted back WITHOUT submitting, and the
failure is shown. It never pretends the switch succeeded.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from typing import Any

from claudepilot import config, install, state, tokens
from claudepilot.router import context
from claudepilot.router.classifier import TIER_ORDER, classify, tier_of_model

PENDING_TTL = 120
MAX_AUTO_CHARS = 4000
PASTE_WRAPPER = re.compile(r"</?pasted_content[^>]*>")


def prompt_hash(prompt: str) -> str:
    """Hash for the auto-mode loop guard, robust to how Claude Code re-wraps typed or pasted
    text (e.g. <pasted_content> tags) and to whitespace changes."""
    norm = " ".join(PASTE_WRAPPER.sub(" ", prompt).split())
    return hashlib.sha256(norm.encode()).hexdigest()


def can_auto_switch(prompt: str, pending: dict[str, Any], now: float) -> str | None:
    """Reason auto mode must NOT block this prompt (falls back to advice), or None."""
    if pending and now - float(pending.get("ts", 0)) < PENDING_TTL:
        return "another auto-switch is still in flight"
    if "\n" in prompt.strip():
        return "multi-line prompt"  # can't be retyped reliably; Enter would submit early
    if len(prompt) > MAX_AUTO_CHARS:
        return "prompt too long to retype"
    if re.search(r"(^|\s)/model\b", prompt):
        return "prompt mentions /model"
    return None


def _pending_path(key: str):
    return state.state_dir() / "panes" / f"{key}.pending.json"


def _context_tokens(payload: dict[str, Any]) -> int:
    ctx = payload.get("context_window") or {}
    try:
        return int(ctx.get("total_input_tokens") or 0)
    except (TypeError, ValueError):
        return 0


def run(data: dict[str, Any]) -> dict[str, Any] | None:
    prompt = data.get("prompt") or ""
    if not prompt.strip() or prompt.lstrip().startswith("/"):
        return None

    key = state.pane_key(data.get("session_id"))
    now = time.time()
    # Recorded for the sidebar's security tips (e.g. bypassPermissions warning).
    if data.get("permission_mode"):
        state.write_json(_meta_path(key), {"permission_mode": data["permission_mode"], "ts": now})
    session = state.read_session_state(key, fallback=False).get("payload") or {}

    routed, note = _route(prompt, key, session, now)
    if routed and routed.get("decision") == "block":
        return routed  # the prompt is resubmitted later; token mode applies then

    out: dict[str, Any] = dict(routed or {})
    level = _token_level(prompt, key, session, now)
    context_parts = [p for p in (note, tokens.INSTRUCTIONS[level] if level else None) if p]
    if context_parts:
        out["hookSpecificOutput"] = {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "\n".join(context_parts),
        }
    return out or None


DELEGATE_NOTES = {
    "haiku": ("[ClaudePilot] This looks like a quick, self-contained ask. If it doesn't need this "
              "conversation's history, delegate it to the claudepilot-quick subagent (Haiku)."),
    "opus": ("[ClaudePilot] This looks hard. If it is self-contained, consider delegating the analysis "
             "to the claudepilot-deep subagent (Opus), then apply its findings here."),
}


def _meta_path(key: str):
    return state.state_dir() / "panes" / f"{key}.meta.json"


def _token_level(prompt: str, key: str, session: dict[str, Any], now: float) -> str | None:
    if config.token_mode() == "off":
        return None
    usage, ctx = tokens.pressure(session, state.read_limits(), now)
    level = tokens.choose_level(prompt, usage, ctx, config.load()["tokens"])
    state.append_event("tokens", pane=key, level=level or "skip", usage=round(usage), ctx=round(ctx))
    return level


def _route(prompt: str, key: str, session: dict[str, Any],
           now: float) -> tuple[dict[str, Any] | None, str | None]:
    """Returns (hook output, note for Claude). The note suggests a delegate subagent."""
    mode = config.router_mode()
    if mode == "off":
        return None, None
    digest = prompt_hash(prompt)

    # Loop guard: this is the prompt our resubmit helper just pasted back.
    pending = state.read_json(_pending_path(key))
    if pending.get("sha") == digest and now - pending.get("ts", 0) < PENDING_TTL:
        _pending_path(key).unlink(missing_ok=True)
        return None, None

    decision = classify(prompt)
    current_model = (session.get("model") or {}).get("id")
    current_tier = tier_of_model(current_model) or tier_of_model(
        (session.get("model") or {}).get("display_name")
    )
    assumed = False
    if current_tier is None:
        # A brand-new session's first prompt can arrive before Claude Code has sent its
        # first statusline update. Fall back to the saved default model, which is what a
        # new session starts on.
        current_tier, assumed = _default_tier(), True
    decision = context.adjust(decision, prompt, current_tier, state.read_events(limit=20, key=key), now)
    ctx_tokens = _context_tokens(session)
    switch_cost = int(config.load()["router"]["switch_cost_tokens"])

    rcfg = config.load()["router"]
    action = "match"
    note = None
    if current_tier and current_tier != decision.tier:
        downgrade = TIER_ORDER[decision.tier] < TIER_ORDER.get(current_tier, 1)
        if ctx_tokens >= switch_cost:
            # Switching re-sends and re-caches the whole conversation on the new model, which
            # can cost more than it saves. Keep the model; a subagent can take the task instead.
            if rcfg.get("delegate", True) and install.agents_available() and (
                    decision.tier in DELEGATE_NOTES):
                action = "delegate"
            elif downgrade:
                action = "stay"
            else:
                action = "switch"  # an upgrade is worth the re-cache when nothing else can help
            if action != "switch":
                note = f"{ctx_tokens // 1000}k tokens in context; switching would forfeit the cache"
        else:
            action = "switch"

    state.append_event(
        "route",
        pane=key,
        mode=mode,
        recommended=decision.tier,
        current=current_tier,
        reason=decision.reason,
        rule=decision.rule,
        words=decision.word_count,
        action=action,
        note=note or ("model assumed from saved default" if assumed and action != "match" else None),
    )

    if action == "delegate":
        return None, DELEGATE_NOTES[decision.tier]
    if action != "switch":
        return None, None

    blocker = can_auto_switch(prompt, pending, now) if mode == "auto" else "advise mode"
    if mode == "auto" and not blocker and not os.environ.get("TMUX_PANE"):
        blocker = "outside the cockpit (auto needs the tmux pane)"
    if mode == "auto" and blocker:
        state.append_event("auto_route", pane=key, to=decision.alias, status="skipped", note=blocker)
    if mode == "auto" and not blocker:
        started = _start_auto_switch(key, prompt, digest, decision.alias)
        if started:
            # Marks the switch as in flight (cascade guard) and lets the retyped prompt through.
            state.write_json(_pending_path(key), {"sha": digest, "ts": now})
            return {
                "decision": "block",
                "reason": (
                    f"ClaudePilot auto-routing: switching to {decision.label} "
                    f"({decision.reason}). Your prompt will be resubmitted automatically."
                ),
            }, None
        # Auto mode needs tmux; fall through to advice.

    arrow = "↓" if TIER_ORDER[decision.tier] < TIER_ORDER.get(current_tier or "sonnet", 1) else "↑"
    return {
        "systemMessage": (
            f"ClaudePilot {arrow} {decision.label} looks like a better fit ({decision.reason}). "
            f"Use /model {decision.alias} to switch."
        )
    }, None


def _default_tier() -> str | None:
    from claudepilot.paths import claude_settings_path

    try:
        return tier_of_model(str(install.load_settings(claude_settings_path()).get("model") or ""))
    except (OSError, ValueError):
        return None


def _start_auto_switch(key: str, prompt: str, digest: str, alias: str) -> bool:
    pane = os.environ.get("TMUX_PANE")
    if not pane or not shutil.which("tmux"):
        return False
    buffer = f"claudepilot-{key}"
    try:
        subprocess.run(
            ["tmux", "set-buffer", "-b", buffer, "--", prompt],
            check=True, capture_output=True, timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    subprocess.Popen(
        [sys.executable, "-m", "claudepilot.hooks", "resubmit",
         "--pane", pane, "--key", key, "--alias", alias, "--buffer", buffer],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return True
