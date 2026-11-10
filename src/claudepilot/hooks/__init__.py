"""Claude Code integration points. Each hook reads JSON on stdin.

Hooks must be fast and must never break Claude Code: every entrypoint catches
all exceptions and exits 0.
"""
