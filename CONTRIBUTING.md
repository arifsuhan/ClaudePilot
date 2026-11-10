# Contributing to ClaudePilot

Thanks for helping! The fastest ways to contribute:

## 1. Improve the router (no Python needed)
Rules live in [`src/claudepilot/data/models.yaml`](src/claudepilot/data/models.yaml).
1. Check how a prompt is classified: `claudepilot classify "your prompt"`
2. Add or adjust a rule (patterns are case-insensitive regexes, highest `weight` wins).
3. Add a case to `tests/test_classifier.py` and run `pytest`.

## 2. Development setup
```bash
git clone <your fork> && cd claudepilot
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest -q && ruff check .
```
Tests never touch your real `~/.claude`: `tests/conftest.py` redirects every path.

## Ground rules
- **Never break Claude Code.** Hooks must be fast, catch every exception and exit 0.
- **Never store prompt text.** Log categories, counts and hashes only.
- **Show only what is true.** Label estimates as estimates, and never show an intended
  model as active before Claude Code confirms it.
- Use only documented Claude Code integration points (statusline, hooks, CLI flags).
