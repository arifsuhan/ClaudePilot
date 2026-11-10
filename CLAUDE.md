# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

ClaudePilot is a tmux "cockpit" around an unmodified Claude Code session. It shows a sidebar with the live model, the 5h and weekly usage limits, prompt-router advice and git status. `idea.md` is the product spec and roadmap. `README.md` is the user-facing pitch.

## Commands

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'   # setup
.venv/bin/pytest -q                                          # all tests
.venv/bin/pytest tests/test_hooks.py::test_stays_when_context_is_large   # single test
.venv/bin/ruff check .                                       # lint (line length 110; B008 ignored for Typer)
claude plugin validate ./plugin/claudepilot                  # validate the Claude Code plugin
.venv/bin/claudepilot classify "some prompt"                 # try the router without storing anything
.venv/bin/claudepilot sidebar --once                         # render the all-in-one sidebar once to stdout
.venv/bin/claudepilot panel files                            # run one interactive panel in this terminal
echo '{...}' | .venv/bin/python -m claudepilot.hooks statusline   # drive a hook by hand
CLAUDEPILOT_DEBUG=1 ...                                      # hooks print tracebacks instead of swallowing them
```

For manual testing, redirect every path so that your real `~/.claude/settings.json` and state stay untouched:
`CLAUDEPILOT_STATE_DIR`, `CLAUDEPILOT_CONFIG_DIR` and `CLAUDEPILOT_CLAUDE_SETTINGS`.
`tests/conftest.py` sets all three automatically and also unsets `TMUX_PANE`/`TMUX`.

## Architecture

**Data flows one way.** Claude Code invokes ClaudePilot's hooks, the hooks write files under the state dir, and the sidebar polls those files. Nothing talks to Claude Code directly except tmux keystrokes in auto mode.

- **Two entrypoints, on purpose.**
  - `claudepilot.cli` (Typer + Rich) is the human-facing CLI.
  - `python -m claudepilot.hooks <name>` (`hooks/__main__.py`) is what `settings.json` calls. It avoids importing typer/rich so hooks start in about 0.1s.

  Every hook catches all exceptions, logs a `hook_error` event and exits 0. A hook must never break Claude Code.
- **`state.py` is the shared bus.**
  - `latest.json` holds the last statusline payload from any session.
  - `limits.json` caches the last reported `rate_limits`. Limits are account-wide, and Claude Code only sends them after a session's first reply (or `/usage`), so `usage.limits.effective_windows()` fills in missing windows from this cache, marked with their age, and shows "reset" once a cached `resets_at` has passed.
  - `panes/<key>.json` holds the last payload per Claude session. Model and context are per session.
  - `events.jsonl` records route, model_switch, auto_route, alert and hook_error events.
  - `usage_history.jsonl` stores the snapshots the forecast reads.

  Session key = `TMUX_PANE` when running inside tmux, else `session_id`, passed through `paths.safe_key` (`%3` → `_3`). `workspace.py` passes the Claude pane id to the sidebar via `--pane` so both sides agree on the key. Writes are atomic (`write_json`). The sidebar and the prompt hook read only their own pane's state (`fallback=False`); falling back to `latest.json` would show, or route on, another session's model.
- **The statusline hook is the data source.** Claude Code's statusline JSON carries `model`, `context_window` and `rate_limits.five_hour/seven_day` (`used_percentage`, `resets_at`; Pro/Max only, after the first reply). The hook persists that JSON, records usage snapshots, and runs `alerts.check` (one alert per threshold per window, tolerating `resets_at` jitter). If the user had their own statusline before `install`, its command is saved in `install.json` and chained: the hook runs it and prints its output.
- **The router (`router/classifier.py` + `data/models.yaml`).**
  - Rules are regexes, and the highest `weight` wins; ties go to the higher tier.
  - Tiers map to Claude Code aliases (`haiku`/`sonnet`/`opus`), so new model releases need no code change.
  - A user override lives at `~/.config/claudepilot/models.yaml`.
  - `hooks/prompt.py` compares the recommendation with the session's current tier. It returns `systemMessage` advice, or `action="stay"` when the suggestion is a cheaper model but the context has reached `switch_cost_tokens` (switching models forfeits the prompt cache).
- **Auto mode.** Hooks cannot change the model. This flow was verified against Claude Code 2.1.322; keep it that way.
  1. `prompt._route` checks `can_auto_switch` (single line, ≤4000 chars, no `/model`, no switch already in flight), blocks the prompt, stashes the text in a tmux buffer, writes `panes/<key>.pending.json` (hash from `prompt_hash`, which normalizes `<pasted_content>` wrappers and whitespace) and spawns `hooks/resubmit.py`.
  2. `resubmit.py` writes `panes/<key>.switching.json` (so `model_switch.pre_run` approves this one switch, skipping the warm-cache dialog), clears the input with `Ctrl-U` ×40 (Claude Code can restore the blocked prompt into the box), types `/model <alias>`, and waits for a `model_switch` event or the statusline.
  3. On confirmation it calls `restore_saved_model` (`/model` saves the default in `settings.json`), retypes the prompt with `send-keys -l` (**never paste**: Claude Code wraps pastes in `<pasted_content>`, which breaks the loop guard and caused a block cascade) and presses Enter. Without confirmation it retypes the prompt but doesn't send it.
- **Conversation-aware routing (`router/context.py`).** It adjusts the single-prompt decision using this pane's recent route events. Acknowledgements and short follow-ups stay on the current model, so "yes" mid-task never drops to Haiku. Failure phrases escalate one tier.
- **Delegation.** When the context is at least `switch_cost_tokens` and the agents are available (`install.agents_available`), the route action is `delegate`, and `DELEGATE_NOTES` goes into `additionalContext`. The agents are in `data/agents/`, installed into `~/.claude/agents/` (files marked `managed by claudepilot`; same-named user files are never touched).
- **One prompt hook, two features (`hooks/prompt.py`).** `_route()` handles the router (a block result returns immediately). `_token_level()` handles token optimization (`tokens.py`), returning `hookSpecificOutput.additionalContext`: "lite" normally, "caveman" above `caveman_usage`/`caveman_context`, and nothing when the prompt asks for detail. Their outputs merge into one JSON. The hook also stores `permission_mode` in `panes/<key>.meta.json` for the guide.
- **Guide (`guide.py` + `data/tips.yaml`).** Contextual tips come from live state (context %, usage pressure, permission mode, `ANTHROPIC_API_KEY`, `explorer.git.unignored_secrets`, Opus overuse) and take priority. Static tips rotate round-robin across categories. Write tips in `tips.yaml` as single-quoted strings: a bare `": "` breaks the YAML, and the loader swallows parse errors (`test_bundled_tips_yaml_parses_and_is_complete` catches it).
- **Secret guard (`hooks/guard.py`, `PreToolUse`).** It is registered with an exact-name matcher (`install.MATCHERS`). It checks path fields and shell-split Bash tokens against `SECRET_NAME`/`SECRET_DIRS`, and returns `permissionDecision` set to the guard mode (`ask`/`deny`). Template suffixes (`.example`, `.pub`, ...) are allowed. It logs only the file's base name. Add a false-positive/negative case to `tests/test_guard.py` when changing patterns.
- **History (`storage/db.py`).** `sync()` copies the JSONL logs into `history.sqlite` (with a 60s overlap; UNIQUE dedupes). The sidebar syncs every 5 min, and `stats`/`wrapped` sync before reading. `compute()` is pure; "followed" means a switch to the recommended tier within 10 min.
- **Plugin (`plugin/claudepilot/`, marketplace at `.claude-plugin/marketplace.json`).** Its hooks call the `claudepilot-hook` console script and exit 0 if it's missing. Keep `hooks.json` and `agents/` in sync with `install.HOOK_EVENTS`/`MATCHERS` and `data/agents/` (`tests/test_v03.py` enforces this). Validate with `claude plugin validate ./plugin/claudepilot`. When the plugin is enabled, `install` registers only the statusline, and `doctor` flags duplicated hooks.
- **`wrapped.py`** is `compute()` (pure, over events plus usage history) followed by `lines()` (shared by the Rich card and `--plain`). `state.MAX_EVENTS` is sized for a week of events. `read_events()` parses only the tail, because the sidebar calls it every second.
- **Install variants:** `install(hooks=False)` (`--statusline-only`) records `"hooks": false` in `install.json`, so `doctor` doesn't report the hooks as missing. `[statusline] style = "full"` makes the statusline print a second ASCII line (`render_full`).
- **Cockpit panels (`panels.py`, `tui.py`, `explorer/tree.py`).** The left column is three tmux panes, each running `claudepilot panel usage|info|files`. `tui.run` is the shared loop (cbreak stdin + `select` + Rich Live). A panel implements `render(console)`, `on_key(key)` and `tick(now)`; `on_key` may return a callable that runs with the terminal handed back (used to run the editor in-pane). `FilesPanel` opens files with `tmux display-popup` when a client is attached, else in the pane. `FileTree` is a pure model (cursor tracked by path, so refreshes keep position). `sidebar.py` now holds the shared renderers plus the all-in-one `sidebar --once` view.
- **CONTROL panel (`panels.ControlPanel`).** Rows 1 Router / 2 Tokens / 3 Guard. Up/down or 1-3 pick a row; left/right/Enter cycle the value through `config.set_mode`, which hooks read on their next call. `explain()` turns route + `auto_route` events into the "Last prompts" lines; every reason auto mode skips a switch must be logged as an `auto_route` event with status `skipped` and a `note`, or the user can't tell why nothing happened.
- **Navigation keys (`workspace.bind_nav_keys`).** Ctrl+arrows and Alt+arrows are bound in tmux's root table, which is server-wide. Each binding is wrapped in `if-shell -F '#{@claudepilot_claude_pane}'` so it passes the key through outside ClaudePilot sessions, and keys the user already bound are skipped. Plain arrows are never used to switch panels: inside a panel they act on that panel only (the user asked for this), and Claude Code needs them in its pane. The layout is four left panes titled `usage`/`control`/`guide`/`files` (`select-pane -T`). Panels highlight focus from `ESC[?1004h` focus events (server option `focus-events on`). When testing, `tmux send-keys -K` fires bindings but does NOT deliver plain keys to panes; use `send-keys -t <pane>` for in-panel keys.
- **Session lifetime (`workspace.create`):** with `close_with_claude` (the default), the Claude pane runs `<claude>; tmux kill-session`, and an `after-kill-pane` hook covers `Ctrl-b x`. Keep `#{...}` formats out of that hook's `run-shell` string, because tmux expands them when the hook fires.
- **Modes:** `config.mode(feature)` covers `router` (default `auto`; outside tmux, auto falls back to advice), `tokens` (default `auto`) and `guard` (default `ask`). `state_dir/<feature>_mode.json` (set by `claudepilot router|tokens|guard ...`) overrides `config.toml`.
- **`install.py`** owns only entries whose command contains `claudepilot.hooks` (`MARKER`). `apply_install`/`apply_uninstall` are pure transforms: install then uninstall must restore the original settings exactly. It refuses to edit invalid JSON and writes a timestamped backup first.

## Performance rules (hooks run on every prompt/tool call; panels run all day)

- Hooks: keep imports out of the hot path. No `importlib.resources` (use `paths.data_file`), `tempfile`, `dataclasses`, `yaml` or `traceback` at module level in anything a hook imports. `tomllib` loads only if `config.toml` exists. Router rules are cached as JSON (`state_dir/rules-cache.json`, keyed by the YAML file's mtime). `install.hook_command` prefers the `claudepilot-hook` console script over `python -m` (which pays for runpy).
- Logs: `state._read_lines` tail-reads from the end of the file and caches by (mtime, size). Never `read_text()` a JSONL log.
- Panels: `tui.run` redraws only when a key, focus, resize or the panel's `signature(now)` changes. A signature must be cheap (`state.file_sig`, a few `stat()`s) and must include a time bucket for anything time-driven (countdowns, tip rotation, periodic git refresh), because `tick()` is skipped while the signature is unchanged. The SQLite sync runs in a subprocess (`claudepilot-hook sync`) so its memory doesn't stay resident.

## Invariants (from the spec; tests enforce some of them)

- Never store or log prompt text. Store only category, word count and hash. `test_prompt_never_stores_text` checks this.
- Show only confirmed state. The active model shown is the one Claude Code reports or `PostModelSwitch` confirms, never the intended one. Forecasts are labelled "est.". The statusline output stays ASCII-only (`test_statusline_is_ascii_safe`), because ambiguous-width glyphs garble it in some terminals. Limits are shown as reported, never hard-coded.
- Use only documented Claude Code integration points (statusline, hooks, CLI). Minimum Claude Code version is v2.1.251, for `PostModelSwitch` and `rate_limits`.
- Router changes in `models.yaml` should come with a case in `tests/test_classifier.py`.
