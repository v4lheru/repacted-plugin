---
description: Set up this machine and repository for rePACTed, the Telegram bridge, or the CLAUDE.md pins
argument-hint: "[--repo] [--teams] | telegram | pin [<content>] | prune"
disable-model-invocation: true
---
# Set up rePACTed

## Modes

The first word of `$ARGUMENTS` picks the mode. Load the named file and run it as written; with no mode word, run the setup below.

- `telegram`: set up the optional Telegram bridge: [reference/init/telegram.md](../reference/init/telegram.md).
- `pin [<content>]`: pin context to the project `CLAUDE.md`, or scan the session for context worth pinning: [reference/init/pin.md](../reference/init/pin.md).
- `prune`: demote one pin from `CLAUDE.md` to long-term memory, chosen page by page: [reference/init/prune.md](../reference/init/prune.md).

## Setup

One setup command. The machine part adds the task-tools flag (`CLAUDE_CODE_ENABLE_TODO_TOOLS`) and allow rules for the plugin's own tools to the user settings (`{config_dir}/settings.json`: `$CLAUDE_CONFIG_DIR` when set, else `~/.claude`). The repo part (`--repo`) adds the `agent` line to `.claude/settings.json`, creates `.repacted/`, and adds the task-log line to `.gitattributes`. Work runs as plain subagents, so no Agent Teams flag is needed. With `--teams` (pass it on when `$ARGUMENTS` has it) the machine part also adds the Agent Teams flag and `REPACT_EXECUTION=teams`. Nothing the user already has is removed or overwritten.

1. Ask the user whether to set up the current repository too, unless `$ARGUMENTS` already says `--repo`.
2. Show what would change, writing nothing:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/bin/repact-init" --dry-run [--repo] [--teams]
   ```

3. Show the user the diff as printed, and any `# kept yours:` lines (their value stays). If it printed `nothing to do`, say so and stop.
4. Ask with `AskUserQuestion` whether to apply. Only on yes:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/bin/repact-init" --yes [--repo] [--teams]
   ```

5. Report the files written, their backups, and the doctor lines it prints last. Changed user settings take effect in a new Claude Code session.

On `refused`, show the message as printed: it names the file, the rule and the way through. Never edit the settings file yourself to get past a refusal.
