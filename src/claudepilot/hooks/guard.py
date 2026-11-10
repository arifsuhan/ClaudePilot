"""PreToolUse secret guard: stop Claude from silently reading secrets.

Detects secret-looking paths in Read/Edit/Write/Grep/Glob inputs and in Bash commands
(`.env`, private keys, `~/.ssh`, `~/.aws/credentials`, `.netrc`, ...).

Modes (config `[guard] mode` or `claudepilot guard ...`):
  ask   (default) Claude Code asks you before the tool runs, showing why
  deny  the call is refused, and Claude sees the reason
  off   no checks

Only the matched file *name* is logged, never file contents or the full command.
Template files (.env.example, .sample, .template, .dist) are allowed.
"""

from __future__ import annotations

import re
import shlex
from pathlib import PurePath
from typing import Any

from claudepilot import config, state

# Matched against each path's final component.
SECRET_NAME = re.compile(
    r"""^(
        \.env(\..+)?                      # .env, .env.local, .env.production
      | .*\.(pem|key|p12|pfx|jks|keystore|asc|gpg)
      | id_(rsa|dsa|ecdsa|ed25519)(_sk)?  # SSH private keys (not .pub)
      | \.netrc | \.pgpass | \.npmrc | \.pypirc | \.git-credentials
      | credentials(\.json)? | secrets?\.(json|ya?ml|toml)
      | service[-_]?account.*\.json
      | .*\.tfstate(\.backup)?
    )$""",
    re.IGNORECASE | re.VERBOSE,
)
SECRET_DIRS = ("/.ssh/", "/.aws/", "/.gnupg/", "/.kube/", "/.docker/config.json", "/.config/gcloud/")
SAFE_SUFFIXES = (".example", ".sample", ".template", ".dist", ".pub")
PATH_FIELDS = ("file_path", "path", "notebook_path", "pattern", "glob")


def find_secret(value: str) -> str | None:
    """Return the secret-looking path inside `value`, or None."""
    value = value.strip().strip("'\"")
    if not value:
        return None
    norm = "/" + value.replace("\\", "/").lstrip("/")
    name = PurePath(value).name
    if name.lower().endswith(SAFE_SUFFIXES):
        return None
    if SECRET_NAME.match(name) or any(d in norm + "/" for d in SECRET_DIRS):
        return value
    return None


def _bash_tokens(command: str) -> list[str]:
    try:
        tokens = shlex.split(command, comments=True)
    except ValueError:
        tokens = command.split()
    out = []
    for tok in tokens:
        # Split redirections and assignments: `<.env`, `--env-file=.env`, `a=$(cat .env)`
        out.extend(t for t in re.split(r"[<>=|;&()`$]", tok) if t)
    return out


def check(tool: str, tool_input: dict[str, Any]) -> str | None:
    if tool == "Bash":
        for tok in _bash_tokens(str(tool_input.get("command") or "")):
            hit = find_secret(tok)
            if hit:
                return hit
        return None
    for field in PATH_FIELDS:
        val = tool_input.get(field)
        if isinstance(val, str):
            hit = find_secret(val)
            if hit:
                return hit
    return None


def run(data: dict[str, Any]) -> dict[str, Any] | None:
    mode = config.guard_mode()
    if mode == "off":
        return None
    tool = data.get("tool_name") or ""
    hit = check(tool, data.get("tool_input") or {})
    if not hit:
        return None
    name = PurePath(hit).name or hit
    state.append_event("guard", pane=state.pane_key(data.get("session_id")), tool=tool,
                       file=name, decision=mode)
    reason = (
        f"ClaudePilot secret guard: {tool} would access '{name}', which looks like a secret. "
        + ("Approve only if you are sure." if mode == "ask"
           else "Blocked. Use a template such as .env.example, or ask the user to share the "
                "specific value that is needed.")
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": mode,
            "permissionDecisionReason": reason,
        }
    }
