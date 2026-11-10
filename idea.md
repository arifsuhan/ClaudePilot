# ClaudePilot — Product Spec

> A tmux cockpit for Claude Code: see your 5-hour and weekly limits, the live model, and a model recommendation for every prompt, without leaving the terminal.

*Unofficial community project. Not affiliated with or endorsed by Anthropic.*

---

## 1. Problem

Claude Code users on Pro / Max plans regularly:

- hit the **5-hour session limit** or **weekly limit** without warning,
- use Opus for prompts that Haiku or Sonnet would answer just as well,
- lose track of which model is active after `/model` changes,
- let the context window fill up until quality degrades,
- switch between terminals to check git status while Claude edits files.

There are good tools for each of these on its own (usage monitors, statusline packs, session managers). None of them combines them into one workspace that also **advises on model choice per prompt**.

## 2. Product principles

1. **Never interfere with Claude Code.** Claude Code runs unmodified in its own pane. ClaudePilot only *observes* (statusline, hooks, transcripts) and *suggests*.
2. **Show only what is true.** Every number is labelled as *reported* (from Claude Code) or *estimated* (computed by ClaudePilot). The active model shown is the one Claude Code reports, never the one we intended.
3. **100% local.** No telemetry and no network calls. Prompt text is classified in-process and never stored.
4. **Useful in 30 seconds.** One install command, zero config, a sensible layout.
5. **Incremental adoption.** Each piece (statusline, router hook, tmux layout) works on its own.

## 3. How it works (grounded in real Claude Code integration points)

```text
                ┌──────────────────────────── Claude Code (unmodified) ───────────────────────────┐
                │                                                                                 │
 statusLine ───►│ stdin JSON: model.id, model.display_name, context_window.used_percentage,       │
 command        │ cost, rate_limits.five_hour / seven_day {used_percentage, resets_at}            │
                │                                                                                 │
 UserPrompt- ──►│ hook receives the prompt → classifier → additionalContext / advice              │
 Submit hook    │                                                                                 │
 PostModel-  ──►│ hook receives from_model / to_model → model-change event                        │
 Switch hook    │                                                                                 │
 PreToolUse  ──►│ hook sees file reads → secret-file guard (.env, keys, ~/.ssh)                   │
 hook           └─────────────────────────────────────────────────────────────────────────────────┘
                                   │ (each writes a small JSON event)
                                   ▼
                     ~/.local/state/claudepilot/  (state.json + events.jsonl)
                                   │
                                   ▼
                 Sidebar panels (Python + Rich, redraw on file change)
```

### Key facts this design relies on

| Need | Supported mechanism |
| --- | --- |
| Current model | Statusline JSON `model.id` / `model.display_name`; `PostModelSwitch` hook |
| 5h / weekly usage | Statusline JSON `rate_limits.five_hour` and `rate_limits.seven_day` (`used_percentage`, `resets_at`). Present only for Pro / Max subscribers, and only after the session's first response |
| Context fill | Statusline JSON `context_window.used_percentage` |
| Per-model token history | Local transcripts `~/.claude/projects/**/*.jsonl` (marked **estimated**) |
| Prompt inspection | `UserPromptSubmit` hook (can add context or block; it **cannot change the model**) |
| Model change | Only `/model`, `--model`, settings, or subagents with a `model:` field. No hook can switch the model |

**Consequence:** Claude Code does not support changing the model per prompt. ClaudePilot treats this honestly:

- **Advise mode** (`claudepilot router advise`; also the fallback outside tmux). The hook classifies the prompt and the sidebar shows *"Simple question → Haiku recommended (you are on Opus)"*.
- **Auto mode (default, experimental).** If the recommendation differs from the active model, the hook blocks the prompt, then sends `/model <x>` to the Claude pane with `tmux send-keys` and resubmits the prompt. Success is confirmed only when `PostModelSwitch` or the statusline reports the new model. Any failure is shown loudly.
- **Delegate mode (future).** Ship ready-made subagents (`quick-haiku`, `deep-opus`) so Claude itself delegates sub-tasks to the right model. This is the officially supported route.

The statusline script does two jobs. It prints a compact status line inside Claude Code, and it saves the JSON it receives to `state.json`, which feeds the sidebar. No scraping and no undocumented APIs are involved.

## 4. Workspace layout

```text
┌──────────────────────────────┬──────────────────────────────────────┐
│ ◆ MODEL & LIMITS             │                                      │
│ Opus · claude-opus-…         │                                      │
│ 5h   ████████░░  78%  1h12m  │                                      │
│ week ████░░░░░░  41%  3d04h  │                                      │
│ ctx  ██░░░░░░░░  18%         │                                      │
│ ⚠ At this pace: 5h limit     │          CLAUDE CODE                 │
│   reached ~15:40             │          (unmodified, full pane)     │
├──────────────────────────────┤                                      │
│ ◆ ROUTER         [ADVISE]    │                                      │
│ last: simple Q → Haiku       │                                      │
│ 💡 Context 82% → try /compact│                                      │
├──────────────────────────────┤                                      │
│ ◆ PROJECT  main ↑2           │                                      │
│ M src/app.ts                 │                                      │
│ A tests/router.test.ts       │                                      │
└──────────────────────────────┴──────────────────────────────────────┘
```

The sidebar width can be configured, and `Ctrl-b z` zooms the Claude pane to full screen. All sidebar panels are a single Python process (`claudepilot sidebar`) with three sections, which keeps it simpler than three separate processes.

## 5. Features

### v0.1 — MVP (the launch) ✅ (implemented)
- `claudepilot`: create or reattach the tmux session with the layout above.
- `claudepilot install`: register the statusline and hooks in `~/.claude/settings.json`, with a backup and a clean `uninstall`.
- **Model & limits panel:** live model, 5h / weekly bars with reset countdowns, and context fill.
- **Limit-hit predictor:** linear burn-rate forecast from recent snapshots (labelled *estimated*).
- **Router (auto mode by default, advise available):** a rule-based classifier with rules in `models.yaml`.
- **Project panel:** git branch, ahead/behind count, changed files. It refreshes on filesystem events, debounced and `.gitignore`-aware.
- `claudepilot doctor`: checks tmux, the claude CLI version, hooks registration and whether a statusline payload has arrived.
- **Threshold alerts:** terminal bell plus `notify-send` / `osascript` at 75% and 90%, and when a limit resets.

### v0.2 — Make it shareable ✅ (implemented)
- `claudepilot wrapped`: a weekly ASCII card showing the model mix, peak hours, limit hits, and Opus prompts the router flagged as simple. Suitable for screenshots.
- **Contextual tips** (shipped early, in v0.1): event-driven, not a static wall of text, for example *"context > 80% → /compact"*, *"Opus used for a one-liner"*, *"large file re-read 3×"*. Tips live in `tips.yaml`.
- **Secret guard:** a `PreToolUse` hook asks (default) or blocks (`claudepilot guard deny`) when a tool touches `.env*`, private keys, `~/.ssh`, `~/.aws` and similar files.
- **Standalone statusline:** `claudepilot install --statusline-only`, plus `[statusline] style = "full"` for bars and the forecast without tmux.

### v0.3 — Smarter routing ✅ (implemented)
- Auto mode (experimental), as described in §3.
- Delegate-mode subagents.
- Conversation-aware routing (escalate after repeated failures, de-escalate for follow-ups).
- Routing history in SQLite, with a "was the advice followed?" metric.
- Packaged as a **Claude Code plugin** (hooks, statusline, `/pilot` slash commands).

### Explicitly out of scope
- Bypassing, pooling or sharing subscription limits.
- Uploading prompts or usage anywhere.
- Hard-coded plan limits. Show what Claude Code reports, and link to Anthropic's docs for plan details.

## 6. CLI

```bash
claudepilot                 # launch or reattach workspace
claudepilot install         # register statusline + hooks (backs up settings.json)
claudepilot uninstall       # restore previous settings
claudepilot usage           # one-shot usage report
claudepilot model           # current model + router mode
claudepilot router advise|auto|off
claudepilot wrapped         # weekly summary card
claudepilot doctor          # health check
claudepilot stop            # kill workspace session
```

## 7. Tech stack

| Concern | Choice | Why |
| --- | --- | --- |
| Language | Python ≥ 3.10 | Easy contributions, `uvx`/`pipx` install |
| UI | Rich (Live) | Lightweight; Textual later if needed |
| CLI | Typer | Auto help, completions |
| Layout | tmux | Ubiquitous, persistent sessions |
| FS events | watchdog | Cross-platform |
| Config | TOML (`~/.config/claudepilot/config.toml`) + YAML rule packs | |
| Storage | JSON state + SQLite history (v0.3) | |
| Tests / lint | pytest, ruff, GitHub Actions | |

```text
claudepilot/
├── src/claudepilot/
│   ├── cli.py            # Typer entrypoint
│   ├── workspace.py      # tmux session/layout
│   ├── install.py        # settings.json patch/unpatch
│   ├── hooks/
│   │   ├── statusline.py # print line + persist state.json
│   │   ├── prompt.py     # UserPromptSubmit → router
│   │   ├── model_switch.py
│   │   └── guard.py      # PreToolUse secret guard
│   ├── router/classifier.py
│   ├── usage/{state.py,forecast.py,transcripts.py}
│   ├── explorer/git.py
│   ├── sidebar.py        # Rich Live UI
│   └── data/{models.yaml,tips.yaml}
├── tests/
├── docs/
└── pyproject.toml
```

## 8. Privacy & security
- Prompt text is classified in memory. Only the category and the chosen model are logged, never the text.
- No credentials are read. ClaudePilot never touches Claude auth files.
- `install` backs up `settings.json` and keeps its edits minimal and reversible.
- History can be deleted with `claudepilot reset --history`.

## 9. Success metrics
- Time from `pipx install` to a working layout: **< 60 seconds**.
- Usage shown in the panel matches `/usage` inside Claude Code.
- Zero reported cases of ClaudePilot breaking a Claude Code session.
- GitHub: a demo GIF in the README and a listing in community "awesome" lists. Stars follow from usefulness.

## 10. Launch checklist
- [ ] 10-second demo GIF (`vhs` tape checked into `docs/demo.tape`)
- [ ] README: hero GIF → one-line install → features → comparison → privacy
- [ ] Social preview image and repo topics (`claude-code`, `tmux`, `cli`, `developer-tools`)
- [ ] `good first issue` labels: new router rules, new tips
- [ ] Posts: r/ClaudeAI, r/commandline, Show HN, dev.to "How the routing hook works"
- [ ] PRs to awesome-claude-code lists
