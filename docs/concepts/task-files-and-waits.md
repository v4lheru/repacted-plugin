# Task files and waits

## Task files

Every [task](../glossary.md#task) is one JSON file on disk:

```
~/.claude/tasks/<team>/<id>.json
```

`~/.claude` is your [config folder](../glossary.md#config-folder) (`$CLAUDE_CONFIG_DIR` when you set it). A task file holds `id`, `subject`, `description`, `status`, `owner` and `metadata`.

The lead makes a work task with `repact dispatch`. Each workflow step after that is one `repact` command, and each command writes its keys in one checked write:

| Key | Written by | What it is |
|---|---|---|
| `dispatch` | `repact dispatch` | Who works on the task, the [variety](../glossary.md#variety) scores, the kind of work, the worktree and branch, and any pinned acceptance criteria |
| `teachback` | `repact submit --teachback`, `repact accept` | The [teachback](teachback.md), and who accepted it |
| `handoff` | `repact submit` | The [HANDOFF](handoff.md) |
| `amend_<n>` | `repact amend` | An [amendment](../glossary.md#amendment): a change to the brief after work started |
| `handoff_rejection` | `repact accept`, `repact reject` | What to fix |
| `acceptance` | `repact accept` | What the checks found, and the test runs on the accepted files |

Do not edit these keys or a task file by hand. A guard refuses a task-tool write or a direct file write that would mark a dispatched task done or change these keys, and it names the `repact` command to use instead. You read a task with `repact inspect <id>`, or one stored object with `repact inspect <id> --key teachback|handoff|rejection|amend`.

A command that stops halfway is safe to run again. `repact inspect` with no task id lists any step that did not finish and the command to rerun.

## Changing a brief mid-task

The lead does not resend a long message. It writes an amendment on the task:

```
repact amend 13 --kind ruling --text "Print to stderr, not stdout."
```

The command prints a message line for a teammate that is still running. The teammate reads open amendments with `repact inspect 13 --key amend`. Its HANDOFF must name each one in `amendments`; `repact submit` refuses it otherwise and prints the amendment text.

## Waiting

With plain subagents, the default, a teammate never waits in a loop. When its teachback needs the lead, it ends its turn, and the lead's `repact accept` prints the `SendMessage` line that resumes it. When it has handed in its HANDOFF, it ends its turn too.

With [Agent Teams](../glossary.md#agent-teams-flag), a teammate does not end its turn while it waits for the lead. It runs the [wait command](../glossary.md#repact-wait) in the foreground:

```
repact-wait <task id> --until completed,rejection,amend
```

It returns on any write to the task file (a [wake](../glossary.md#wake)) and prints one line: the time, the status and the names of changed metadata keys. With `--until`, it returns at once if the task is already completed, rejected or amended. Exit codes: `0` changed, `2` usage or team not found, `3` timeout (run it again), `4` task file missing.

Before it waits, the teammate writes a [wait marker](../glossary.md#wait-marker), so anyone can see why it is idle, and clears it when the wait ends. Filler calls such as a bare `sleep 30` or `true` are refused. Wait on the task file instead.

`repact-wait`, `repact-status` and `pact-which-team` work only with Agent Teams; with subagents they say so and exit.

## The session journal

Dispatches, acceptances, rejections and refusals also go to the [session journal](../glossary.md#session-journal) under `~/.claude/pact-sessions/`.
