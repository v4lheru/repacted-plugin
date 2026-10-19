---
description: Start work on an ask, on the next task-log row, or on the whole task log unattended
argument-hint: "[<ask>] | --unattended [--until HH:MM] [--max-usd N] [--max-tasks N] [--pr]"
disable-model-invocation: true
---
# Work

The front door for getting something done. Use it when you have an ask, or when you want the next task from the task log.

- `$ARGUMENTS` is an ask: pick the lane from its complexity (persona §13): the §8 direct-edit lane for a small known change, `/repacted:comPACT` for 4-6, `/repacted:orchestrate` for 7+, with `/repacted:plan-mode` first at 11+.
- `$ARGUMENTS` is empty: take the next task-log row as [the task-log page](../reference/work/task-log.md) says.
- `$ARGUMENTS` starts with `--unattended`: run `/repacted:autopilot` with the rest (`--until`, `--max-usd`, `--max-tasks`, `--pr`).

From a terminal, `repact work "<ask>"`, `repact work` and `repact work --unattended ...` start a new session the same way. Inside a session `repact work` refuses, so use this command there.
