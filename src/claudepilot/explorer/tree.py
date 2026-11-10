"""File tree model for the Files panel: a collapsible tree with a cursor.

File lists come from git (`ls-files --cached --others --exclude-standard`), so .gitignore
is respected. Outside a git repo it falls back to a shallow directory walk.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

WALK_LIMIT = 5000
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}


@dataclass(frozen=True)
class Row:
    path: str  # relative posix path; directories without a trailing slash
    name: str
    depth: int
    is_dir: bool
    expanded: bool = False
    status: str = ""  # git code for files; "*" for a directory containing changes


def list_files(cwd: Path) -> list[str]:
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=cwd, capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0:
            # Tracked files deleted from disk still show up in --cached: drop them.
            return sorted(p for p in proc.stdout.split("\0") if p and (cwd / p).exists())
    except (OSError, subprocess.TimeoutExpired):
        pass
    out: list[str] = []
    for root, dirs, files in os.walk(cwd):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        rel = Path(root).relative_to(cwd)
        for f in sorted(files):
            out.append((rel / f).as_posix())
            if len(out) >= WALK_LIMIT:
                return out
    return out


class FileTree:
    def __init__(self, files: list[str] | None = None, statuses: dict[str, str] | None = None):
        self.expanded: set[str] = set()
        self.cursor_path: str | None = None
        self.offset = 0
        self._children: dict[str, tuple[list[str], list[str]]] = {}
        self._status: dict[str, str] = {}
        self._dirty_dirs: set[str] = set()
        self.rows: list[Row] = []
        self.update(files or [], statuses or {})

    # --- data ---------------------------------------------------------------
    def update(self, files: list[str], statuses: dict[str, str]) -> None:
        """Replace the file list, keeping expansion and the cursor where possible."""
        dirs: dict[str, set[str]] = {"": set()}
        leaves: dict[str, list[str]] = {"": []}
        for f in files:
            parts = f.split("/")
            parent = ""
            for part in parts[:-1]:
                child = f"{parent}/{part}" if parent else part
                dirs.setdefault(parent, set()).add(child)
                dirs.setdefault(child, set())
                leaves.setdefault(child, [])
                parent = child
            leaves.setdefault(parent, []).append(f)
        self._children = {d: (sorted(dirs[d]), sorted(leaves.get(d, []))) for d in dirs}
        self._status = dict(statuses)
        self._dirty_dirs = set()
        for path in statuses:
            parts = path.rstrip("/").split("/")
            for i in range(1, len(parts) + (1 if path.endswith("/") else 0)):
                self._dirty_dirs.add("/".join(parts[:i]))
        self.expanded &= set(self._children)
        self._rebuild()

    def _status_for(self, path: str) -> str:
        """Git code for a path. Porcelain reports an untracked directory once ("docs/"),
        so files inside it inherit that code."""
        if path in self._status:
            return self._status[path]
        parts = path.split("/")
        for i in range(len(parts), 0, -1):
            code = self._status.get("/".join(parts[:i]) + "/")
            if code:
                return code
        return ""

    def _rebuild(self) -> None:
        rows: list[Row] = []

        def walk(directory: str, depth: int) -> None:
            sub, files = self._children.get(directory, ([], []))
            for d in sub:
                is_open = d in self.expanded
                rows.append(Row(d, d.rsplit("/", 1)[-1], depth, True, is_open,
                                "*" if d in self._dirty_dirs or self._status_for(d) else ""))
                if is_open:
                    walk(d, depth + 1)
            for f in files:
                rows.append(Row(f, f.rsplit("/", 1)[-1], depth, False, False, self._status_for(f)))

        walk("", 0)
        self.rows = rows
        if self.cursor >= len(rows):
            self.cursor_path = rows[-1].path if rows else None

    # --- cursor -------------------------------------------------------------
    @property
    def cursor(self) -> int:
        for i, row in enumerate(self.rows):
            if row.path == self.cursor_path:
                return i
        return 0

    @property
    def current(self) -> Row | None:
        return self.rows[self.cursor] if self.rows else None

    def move(self, delta: int) -> None:
        if self.rows:
            i = max(0, min(len(self.rows) - 1, self.cursor + delta))
            self.cursor_path = self.rows[i].path

    def move_to(self, index: int) -> None:
        if self.rows:
            self.cursor_path = self.rows[max(0, min(len(self.rows) - 1, index))].path

    def toggle(self, expand: bool | None = None) -> None:
        row = self.current
        if not row or not row.is_dir:
            return
        want = (row.path not in self.expanded) if expand is None else expand
        (self.expanded.add if want else self.expanded.discard)(row.path)
        self._rebuild()

    def collapse_or_parent(self) -> None:
        """Left arrow: close an open directory, otherwise jump to the parent directory."""
        row = self.current
        if not row:
            return
        if row.is_dir and row.expanded:
            self.toggle(False)
        elif "/" in row.path:
            self.cursor_path = row.path.rsplit("/", 1)[0]

    def window(self, height: int) -> tuple[list[Row], int]:
        """Rows visible in `height` lines (scrolled to keep the cursor in view) and the
        cursor's index within them."""
        height = max(1, height)
        cur = self.cursor
        if cur < self.offset:
            self.offset = cur
        elif cur >= self.offset + height:
            self.offset = cur - height + 1
        self.offset = max(0, min(self.offset, max(0, len(self.rows) - height)))
        return self.rows[self.offset:self.offset + height], cur - self.offset
