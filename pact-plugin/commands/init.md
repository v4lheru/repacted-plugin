---
description: Set up this machine for rePACTed and, optionally, the current repository
argument-hint: "[--repo]"
disable-model-invocation: true
---
# Set up rePACTed

One setup command. The machine part adds the Agent Teams flag and allow rules for the plugin's own tools to the user settings (`{config_dir}/settings.json`: `$CLAUDE_CONFIG_DIR` when set, else `~/.claude`). The repo part (`--repo`) adds the `agent` line to `.claude/settings.json`, creates `.repacted/`, and adds the task-log line to `.gitattributes`. Nothing the user already has is removed or overwritten.

1. Ask the user whether to set up the current repository too, unless `$ARGUMENTS` already says `--repo`.
2. Show what would change, writing nothing:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/bin/repact-init" --dry-run [--repo]
   ```

3. Show the user the diff as printed, and any `# kept yours:` lines (their value stays). If it printed `nothing to do`, say so and stop.
4. Ask with `AskUserQuestion` whether to apply. Only on yes:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/bin/repact-init" --yes [--repo]
   ```

5. Report the files written, their backups, and the doctor lines it prints last. Changed user settings take effect in a new Claude Code session.

On `refused`, show the message as printed: it names the file, the rule and the way through. Never edit the settings file yourself to get past a refusal.
