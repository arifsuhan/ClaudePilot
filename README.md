<div align="center">

# ✈️ ClaudePilot

**A tmux cockpit for Claude Code.**
Live model · 5h & weekly limits · per-prompt model routing · token saving · secret guard · file explorer, all in one terminal.

<!-- TODO: replace with demo GIF recorded via `vhs docs/demo.tape` -->
![ClaudePilot demo](docs/demo.gif)

[Setup](#-setup) · [How to use](#-how-to-use) · [CLI commands](#-cli-commands) · [Features](#-features) · [Configuration](#-configuration) · [Troubleshooting](#-troubleshooting)

*Unofficial community project. Not affiliated with Anthropic.*

</div>

---

## Why?

You're deep in a Claude Code session, and then:

- 🛑 *"You've reached your usage limit"*, with no warning beforehand.
- 💸 The last 20 one-line questions all went to Opus.
- 🤔 *Which model am I on right now?*
- 🧠 Context is 90% full and answers are getting worse.

ClaudePilot keeps all of that visible next to Claude Code and acts **before** it becomes a problem.

```text
┌─ USAGE ─────────────────────┬───────────────────────────────────────┐
│ Status Config Usage Stats   │                                       │
│ 5h   ███████░░░░  62% 2h46m │                                       │
│ week █████░░░░░░  42% 9h56m │                                       │
├─ CONTROL ───────────────────┤                                       │
│ Router AUTO  Tokens AUTO    │            CLAUDE CODE                │
│ Guard ASK                   │       (unmodified, full pane)         │
├─ GUIDE ─────────────────────┤                                       │
│ ! .env not gitignored ...   │                                       │
├─ FILES  my-project/ ────────┤                                       │
│ main  M2 A1 D0 ?1           │                                       │
│ * - src/                    │                                       │
│ M     app.py                │                                       │
└─────────────────────────────┴───────────────────────────────────────┘
```

---

## 🚀 Setup

### Requirements

- Linux or macOS with **tmux** (3.x)
- **Python 3.10+** and [pipx](https://pipx.pypa.io)
- **Claude Code v2.1.251+**. Usage limits appear for **Pro / Max** plans.

### 1. Install the package

ClaudePilot is not on PyPI yet, so install it from source:

```bash
git clone https://github.com/arifsuhan/ClaudePilot && cd ClaudePilot
pipx install -e .
```

When the PyPI release is out, this becomes `pipx install claudepilot`.

### 2. Connect it to Claude Code

```bash
claudepilot install
```

This command:
- backs up `~/.claude/settings.json`
- registers the statusline and the hooks (`UserPromptSubmit`, `PreModelSwitch`, `PostModelSwitch`, `PreToolUse`)
- installs two helper subagents into `~/.claude/agents/`

Your own hooks are left alone. An existing statusline is kept and runs inside ClaudePilot's.

### 3. Check and launch

```bash
claudepilot doctor     # every line should be ✓ (or ! for optional items)
claudepilot            # open the cockpit for the current directory
```

Restart any Claude Code sessions that were already running so they pick up the hooks.

<details>
<summary><b>Alternative: install as a Claude Code plugin</b></summary>

The plugin provides the hooks, the subagents and slash commands. It still needs the Python package, and it can't set the statusline:

```bash
pipx install -e .                              # from the cloned repo
claudepilot install --statusline-only
```

Then, inside Claude Code:

```
/plugin marketplace add arifsuhan/ClaudePilot
/plugin install claudepilot@claudepilot
```

Slash commands: `/claudepilot:usage`, `/claudepilot:wrapped`, `/claudepilot:stats`, `/claudepilot:doctor`, `/claudepilot:mode`.
</details>

<details>
<summary><b>Without tmux</b></summary>

You can use only the statusline under Claude Code's prompt:

```bash
claudepilot install --statusline-only
```

Add `[statusline] style = "full"` to the config for a second line with usage bars, the forecast and modes.
</details>

### Update / uninstall

```bash
pipx install -e --force .    # after pulling a new version
claudepilot install          # re-register hooks (safe to repeat)

claudepilot uninstall        # removes ClaudePilot from settings.json and restores your statusline
pipx uninstall claudepilot
```

---

## 🧭 How to use

### Start

Run `claudepilot` in a project folder. It opens a tmux session with Claude Code on the right and four panels on the left. Running it again in the same folder reattaches. Closing Claude Code (`/exit`, `Ctrl-D`) closes the whole cockpit.

### Keys

| Key | Where | What it does |
| --- | --- | --- |
| `Ctrl+←/→/↑/↓` | anywhere | Switch panel: Usage, Control, Guide, Files, Claude Code |
| `Alt+arrows` / mouse click | anywhere | Also switch panels |
| `Esc` | any panel | Back to Claude Code |
| `←/→` or `1`–`4` | Usage | Switch tab: Status, Config, Usage, Stats |
| `↑/↓` or `1`–`3` | Control | Pick a setting: 1 Router, 2 Tokens, 3 Guard |
| `←/→` or `Enter` | Control | Change the selected setting (applies immediately) |
| `↑/↓`, `PgUp/PgDn`, `Home/End` | Files | Move through the file tree |
| `Enter` | Files | Open the file in vim (popup), or fold/unfold a folder |
| `←/→` | Files | Collapse / expand a folder |

The focused panel has a highlighted title. Inside the cockpit, `Ctrl+←/→` switch panels instead of jumping words in Claude Code; `Alt+B` / `Alt+F` still jump words. Mouse mode is on, so hold **Shift** while dragging to select text.

### Panels

- **Usage.** Four tabs:
  - *Status:* Claude Code version, model, effort, permission mode, cache, hook health
  - *Config:* current modes and thresholds
  - *Usage:* 5h and weekly bars, reset countdowns, forecast, context size
  - *Stats:* 7 days of prompts, model mix, peaks, router results

  It mirrors what Claude Code reports. Claude Code's own dialog is still `/usage` in its pane.
- **Control.** Change Router (`off/advise/auto`), Tokens (`off/auto`) and Guard (`off/ask/deny`) in place. *Last prompts* explains each routing decision, e.g. *"kept opus, not sonnet: 450k ctx"* or *"switched opus -> haiku"*.
- **Guide.** Warnings when they matter (context almost full, permissions bypassed, `.env` not gitignored, an API key that would bill the API), plus rotating security, policy and token tips.
- **Files.** The project tree (respects `.gitignore`) with git marks: `M` modified, `A` added, `D` deleted, `?` untracked, `*` folder with changes.

### Everyday workflow

1. **Work in Claude Code as usual.** The router picks Haiku, Sonnet or Opus per prompt. In auto mode it switches for you; it only reports a switch after Claude Code confirms it.
2. **Watch the Usage panel.** A forecast like *"At this pace: 5h limit ~15:40"* tells you when to slow down. You get desktop alerts at 75% and 90%.
3. **Let token mode work.** Answers stay concise. When usage or context runs high, they become ultra-terse ("caveman" style). Prompts that ask for detail are left alone.
4. **Check your week** with `claudepilot wrapped` or `claudepilot stats`.

To get suggestions without automatic switching, set Router to `advise` in the Control panel (or run `claudepilot router advise`).

> **Automatic switching only happens for prompts typed in the cockpit's Claude pane**, because ClaudePilot has to type `/model` there. In other Claude Code windows you get a one-line suggestion instead. With a very large conversation (150k+ tokens) it keeps the current model, since switching would re-send everything. Use `/clear` between tasks so routing can kick in again.

---

## 💻 CLI commands

### Workspace

| Command | What it does |
| --- | --- |
| `claudepilot` | Launch or reattach the cockpit for the current directory |
| `claudepilot start [--no-attach]` | Create the cockpit (reuses an existing one) |
| `claudepilot attach` | Reattach to this directory's cockpit |
| `claudepilot stop` | Stop this directory's cockpit |

### Setup and health

| Command | What it does |
| --- | --- |
| `claudepilot install` | Register statusline, hooks and subagents (backs up `settings.json`) |
| `claudepilot install --statusline-only` | Statusline only; no hooks |
| `claudepilot install --no-agents` | Skip the helper subagents |
| `claudepilot uninstall` | Remove everything ClaudePilot added |
| `claudepilot doctor` | Check tmux, Claude Code version, hooks, data and modes |

### Modes

| Command | What it does |
| --- | --- |
| `claudepilot router off\|advise\|auto` | Model routing: off, suggest only, or switch automatically (default `auto`) |
| `claudepilot tokens off\|auto` | Token optimization (default `auto`) |
| `claudepilot guard off\|ask\|deny` | Secret guard: ask before touching secret files, or block (default `ask`) |
| `claudepilot model` | Show the current model and all modes |

### Usage and history

| Command | What it does |
| --- | --- |
| `claudepilot usage` | 5h / weekly usage, reset countdown and forecast |
| `claudepilot stats [--days 30]` | Daily model mix, usage peaks, router results |
| `claudepilot wrapped [--days 7] [--plain]` | Your week as a shareable card (no prompt text) |
| `claudepilot classify "<prompt>"` | Show how a prompt would be routed (nothing is stored) |
| `claudepilot reset --history` | Delete local usage and router history |

### Internal (started by the cockpit / Claude Code)

| Command | What it does |
| --- | --- |
| `claudepilot panel usage\|control\|guide\|files` | Run one panel in the current terminal |
| `claudepilot sidebar --once` | Print all panels once |
| `claudepilot statusline` | Statusline entrypoint (reads Claude Code's JSON on stdin) |
| `claudepilot-hook <name>` | Hook entrypoint used by `settings.json` and the plugin |

---

## ✨ Features

### Usage and model

| | |
| --- | --- |
| 🎯 **Live model** | The model Claude Code *actually* reports, updated on every switch |
| ⏳ **5h & weekly limits** | Usage bars and reset countdowns from Claude Code itself, shown from the moment a session starts |
| 🔮 **Limit forecast** | *"At this pace you hit your 5h limit at 15:40"*, labelled as an estimate |
| 🔔 **Alerts** | Desktop notification at 75% / 90%, and when a limit resets |

### Routing and saving

| | |
| --- | --- |
| 🧭 **Prompt router** | Picks Haiku / Sonnet / Opus per prompt. Auto mode switches for you; advise mode suggests |
| 💬 **Conversation-aware** | "yes, go ahead" stays on the current model; "still failing" escalates one tier |
| 💾 **Cache-aware** | With a large context it won't switch (that would re-send everything); it suggests a helper subagent instead |
| 🤝 **Helper subagents** | `claudepilot-quick` (Haiku, lookups) and `claudepilot-deep` (Opus, hard analysis) |
| ✂️ **Token optimization** | Concise answers by default, ultra-terse "caveman" mode under pressure. Code, commands, paths and errors are never shortened |

### Safety

| | |
| --- | --- |
| 🔐 **Secret guard** | Claude Code asks before any tool touches `.env`, private keys, `~/.ssh`, `~/.aws`, `.netrc`, `*.tfstate` and similar. Templates like `.env.example` are allowed |
| 🛡️ **Guide warnings** | Bypassed permissions, unignored secrets, `ANTHROPIC_API_KEY` billing the API, nearly full context |

### Workspace

| | |
| --- | --- |
| 🖥️ **Cockpit** | One command, five panes, one tmux session per project; closes with Claude Code |
| 🌿 **Files panel** | Scrollable tree with git marks; Enter opens vim in a popup |
| 📋 **Usage panel** | Status, Config, Usage and Stats tabs |
| 📟 **Statusline** | Compact one-line or two-line `full` style; works without tmux |

### History

| | |
| --- | --- |
| 📊 **`stats`** | Local SQLite history: model mix, peaks, how often router advice was followed |
| 🎁 **`wrapped`** | Weekly shareable card: model mix, prompts that could have used a smaller model, limit hits |
| 🧩 **Plugin** | Hooks, subagents and slash commands as a Claude Code plugin |

---

## ⚙️ Configuration

`~/.config/claudepilot/config.toml`. Every key is optional; see [`docs/config.example.toml`](docs/config.example.toml).

```toml
[router]
mode = "auto"              # off | advise | auto
switch_cost_tokens = 150000 # above this context size, suggest a subagent instead of switching
delegate = true

[tokens]
mode = "auto"              # off | auto
caveman_usage = 75         # ultra-terse at this 5h/weekly usage %
caveman_context = 70       # ...or at this context %

[guard]
mode = "ask"               # off | ask | deny

[alerts]
thresholds = [75, 90]

[workspace]
sidebar_width = 42
claude_command = "claude"  # e.g. "claude --model sonnet"
close_with_claude = true
nav_keys = true            # Ctrl/Alt+arrows switch panels
mouse = true

[files]
editor = "vim"

[statusline]
style = "compact"          # compact | full
```

Mode commands (`claudepilot router ...`) override the file. Router rules and tips can be customized by copying [`models.yaml`](src/claudepilot/data/models.yaml) or [`tips.yaml`](src/claudepilot/data/tips.yaml) to `~/.config/claudepilot/`.

---

## 🔧 How it works

ClaudePilot **never wraps or modifies Claude Code**. It only uses documented extension points:

- **Statusline:** receives model, context and `rate_limits` JSON and saves it for the panels.
- **`UserPromptSubmit` hook:** routing and token mode.
- **`PreModelSwitch` / `PostModelSwitch` hooks:** approve only ClaudePilot's own switches and record confirmed ones.
- **`PreToolUse` hook:** the secret guard (checks file names, never contents).

> **Auto mode, in detail:** hooks can't change the model. So ClaudePilot does it in steps:
> 1. Blocks the prompt and clears the input box.
> 2. Types `/model <tier>` and waits until Claude Code **confirms** the switch.
> 3. Retypes your prompt and submits it. If the switch isn't confirmed, the prompt is put back unsent.
>
> `/model` also saves your choice as the default for future sessions, so ClaudePilot restores your saved default afterwards. It falls back to a suggestion outside tmux, for multi-line prompts, and while another switch is in flight.

## 🔒 Privacy

- 100% local: no telemetry, no network calls.
- Prompt text is classified in memory and **never stored**. Logs hold categories, counts and hashes.
- Never reads your Claude credentials.
- `claudepilot uninstall` restores your original settings.

---

## 🩺 Troubleshooting

| Problem | Fix |
| --- | --- |
| Panel switching does nothing in **VS Code** | Add `"terminal.integrated.sendKeybindingsToShell": true` to VS Code settings, or click a panel |
| Status tab says **hooks outdated** | Run `claudepilot install`, then restart Claude Code |
| **"Usage appears after the first reply"** | Limits come from Claude Code after the first reply (Pro / Max only); send a prompt or run `/usage` |
| Cockpit looks old after an update | `claudepilot stop && claudepilot` |
| Something else | `claudepilot doctor`; set `CLAUDEPILOT_DEBUG=1` to print hook errors |

---

## 🆚 How it compares

| | ClaudePilot | Usage monitors (e.g. ccusage) | Statusline packs | Session managers |
| --- | :-: | :-: | :-: | :-: |
| Live 5h / weekly limits | ✅ | ✅ | some | — |
| Per-prompt model routing | ✅ | — | — | — |
| Token optimization | ✅ | — | — | — |
| Secret guard | ✅ | — | — | — |
| Git / file panel | ✅ | — | some | some |
| One-command workspace | ✅ | — | — | ✅ |

## 🤝 Contributing

The easiest contribution is a **router rule** in [`models.yaml`](src/claudepilot/data/models.yaml) or a **tip** in [`tips.yaml`](src/claudepilot/data/tips.yaml), with no Python needed. See [CONTRIBUTING.md](CONTRIBUTING.md).

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q && .venv/bin/ruff check .
```

## License

MIT
