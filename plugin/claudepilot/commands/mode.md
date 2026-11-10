---
description: Show or set ClaudePilot modes, e.g. "router advise", "tokens off", "guard deny"
argument-hint: "[router off|advise|auto] [tokens off|auto] [guard off|ask|deny]"
allowed-tools: Bash(claudepilot router:*), Bash(claudepilot tokens:*), Bash(claudepilot guard:*), Bash(claudepilot model)
---
Arguments: "$ARGUMENTS"

If the arguments are empty, run `claudepilot model` and show its output. Otherwise run the
matching command (`claudepilot router <mode>`, `claudepilot tokens <mode>` or
`claudepilot guard <mode>`) and reply with one line confirming the new mode.
