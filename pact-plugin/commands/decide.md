---
description: List every question that waits on you, numbered, and answer a task-log one
argument-hint: "[<n | T-ref> set|done|drop|dep|reopen ...]"
disable-model-invocation: true
---
# Decide

Use it when work has stopped on you: a task-log row that needs your say, a parked task, an unattended halt or a held knowledge-base item.

Run `repact decide $ARGUMENTS` and show its lines as printed. With no argument it lists the questions, numbered. `repact decide <n|T-ref> <verb> ...` answers a task-log row (`set`, `done`, `drop`, `dep`, `reopen`); a number names an item of the last listing. A halt, a parked task or a held KB item is not answered here: the command prints what to run instead, and a held KB item is decided only at your own terminal.

## A teammate's blocker

When a teammate reports a blocker, or the same failure keeps coming back, load [blocker triage](../reference/decide/blocker-triage.md) and run it as written. You saw the work stop, so set its task-log row (the work task's `metadata.project_task`) to `blocked` without asking, and back to `active` when the work resumes:

```bash
python3 "{plugin_root}/bin/repact-task" set <ref> --status blocked
python3 "{plugin_root}/bin/repact-task" set <ref> --status active
```
