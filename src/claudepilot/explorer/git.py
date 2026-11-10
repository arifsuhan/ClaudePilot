"""Git status for the project panel. Uses the git CLI, which respects .gitignore."""

from __future__ import annotations

import contextlib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class GitStatus:
    is_repo: bool
    branch: str | None = None
    upstream: str | None = None
    ahead: int = 0
    behind: int = 0
    changes: list[tuple[str, str]] = field(default_factory=list)  # (code, path)
    last_commit: str | None = None

    @property
    def counts(self) -> dict[str, int]:
        out = {"M": 0, "A": 0, "D": 0, "?": 0}
        for code, _ in self.changes:
            for c in ("M", "A", "D", "?"):
                if c in code:
                    out[c] += 1
                    break
        return out


def parse_porcelain(text: str) -> GitStatus:
    """Parse `git status --porcelain=v1 -b`."""
    status = GitStatus(is_repo=True)
    for line in text.splitlines():
        if line.startswith("## "):
            _parse_branch(line[3:], status)
        elif len(line) > 3:
            code, path = line[:2].strip() or "?", line[3:]
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            status.changes.append(("?" if code == "??" else code, path))
    return status


def _parse_branch(header: str, status: GitStatus) -> None:
    if header.startswith("No commits yet on "):
        status.branch = header.removeprefix("No commits yet on ")
        return
    info = ""
    if " [" in header and header.endswith("]"):
        header, info = header[:-1].split(" [", 1)
    if "..." in header:
        status.branch, status.upstream = header.split("...", 1)
    else:
        status.branch = header
    for part in info.split(", "):
        if part.startswith("ahead "):
            status.ahead = int(part[6:])
        elif part.startswith("behind "):
            status.behind = int(part[7:])


def read_status(cwd: Path) -> GitStatus:
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain=v1", "-b"],
            cwd=cwd, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return GitStatus(is_repo=False)
    if proc.returncode != 0:
        return GitStatus(is_repo=False)
    status = parse_porcelain(proc.stdout)
    try:
        log = subprocess.run(
            ["git", "log", "-1", "--format=%h %s (%cr)"],
            cwd=cwd, capture_output=True, text=True, timeout=5,
        )
        status.last_commit = log.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        pass
    return status


SECRET_GLOBS = (".env", ".env.*", "*.pem", "*.key", "id_rsa", "id_ed25519")
SAFE_SUFFIXES = (".example", ".sample", ".template", ".dist")


def unignored_secrets(cwd: Path) -> list[str]:
    """Secret-looking files in the project root that git would NOT ignore (names only)."""
    candidates = sorted({
        p.name for pattern in SECRET_GLOBS for p in cwd.glob(pattern)
        if p.is_file() and not p.name.endswith(SAFE_SUFFIXES)
    })
    if not candidates:
        return []
    try:
        proc = subprocess.run(
            ["git", "check-ignore", "--", *candidates],
            cwd=cwd, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode not in (0, 1):  # 128: not a git repo, so nothing gets committed
        return []
    ignored = set(proc.stdout.split())
    return [c for c in candidates if c not in ignored]


def git_dir_mtime(cwd: Path) -> float:
    """Cheap change signal: index + HEAD mtimes. Avoids running git every tick."""
    total = 0.0
    for rel in (".git/index", ".git/HEAD"):
        with contextlib.suppress(OSError):
            total += (cwd / rel).stat().st_mtime
    return total
