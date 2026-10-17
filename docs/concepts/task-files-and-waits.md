# Task files and waits

## Task files

Every [task](../glossary.md#task) is one JSON file on disk:

```
~/.claude/tasks/<team>/<id>.json
```

`~/.claude` is your [config folder](../glossary.md#config-folder) (`$CLAUDE_CONFIG_DIR` when you set it). A task file holds `id`, `subject`, `description`, `status`, `owner`, `blocks`, `blockedBy` and `metadata`.

The protocol lives in `metadata`. These are the keys you will see most:

| Key | Who writes it | What it is |
|---|---|---|
| `variety` | lead | The scores for the work task ([variety](../glossary.md#variety)) |
| `teachback_submit` | teammate | The [teachback](teachback.md) |
| `handoff` | teammate | The [HANDOFF](handoff.md) |
| `intentional_wait` | teammate | The [wait marker](../glossary.md#wait-marker) |
| `amend_<n>` | lead | An [amendment](../glossary.md#amendment): a change to the brief after work started |
| `teachback_rejection`, `handoff_rejection` | lead | What to fix |

The task file is the one place both sides trust. A `SendMessage` reaches a busy teammate only at the end of its turn. A write to its task file wakes it at once.

## Waiting

A teammate does not end its turn when it waits for the lead. It runs the [wait command](../glossary.md#repact-wait) in the foreground:

```
repact-wait <task id> --until completed,rejection,amend
```

It returns on any write to the task file (a [wake](../glossary.md#wake)) and prints one line: the time, the status, `blockedBy` and the names of changed metadata keys. With `--until`, it returns at once if the task is already completed, rejected or amended. Exit codes: `0` changed, `2` usage or team not found, `3` timeout (run it again), `4` task file missing.

Before it waits, the teammate writes a wait marker, so anyone can see why it is idle:

```json
{"intentional_wait": {"reason": "awaiting_lead_completion", "expected_resolver": "lead",
  "since": "2026-01-15T09:30:00+00:00", "covers_since": "2026-01-15T09:30:00+00:00"}}
```

When the wait ends, the teammate clears the marker by setting it to `null`.

Filler calls such as a bare `sleep 30` or `true` are refused. Wait on the task file instead.

## Example

A teammate submitted its teachback on task 12 and waits. The lead accepts by completing the task. The wait returns:

```
2026-01-15T09:31:02Z #12 changed: status completed (was in_progress); blockedBy none; metadata added: none; changed: none; removed: none
```

## Changing a brief mid-task

The lead does not resend a long message. It writes an amendment on the task. `repact-amend` prints the next free key and the object to paste:

```
repact-amend next 13 --kind ruling --text "Print to stderr, not stdout."
```

The teammate reads open amendments with `repact-payload 13 --key amend`, and its HANDOFF names each one in `amendments`.

## The session journal

Waits, wakes and refusals also go to the [session journal](../glossary.md#session-journal) under `~/.claude/pact-sessions/`.
