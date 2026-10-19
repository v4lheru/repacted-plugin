# Lead and teammates

## The lead

The [lead](../glossary.md#lead) is the agent in your main Claude Code session. Its agent name is `repacted:pact-orchestrator`. The lead plans the work, splits it into [tasks](../glossary.md#task), hands each task to a teammate and checks every result. It edits code itself only for small, known changes. Everything else goes to a specialist.

The lead is also the only one who accepts work. It runs `repact accept`, which completes a task only when the checks pass. A teammate never completes its own task.

You start the lead in one of two ways:

- `claude --agent repacted:pact-orchestrator`
- once per project, `/repacted:init --repo`, which adds `"agent": "repacted:pact-orchestrator"` to `.claude/settings.json`

## Teammates

A [teammate](../glossary.md#teammate) is an agent that the lead starts for one task. Each one runs in its own context, so it does not fill up the lead's context. Most teammates are [specialists](../glossary.md#specialist) with one job:

| Agent | Job |
|---|---|
| `pact-preparer` | Researches docs, APIs and best practices before design or code |
| `pact-architect` | Designs components, contracts and data flows |
| `pact-backend-coder`, `pact-frontend-coder`, `pact-database-engineer`, `pact-devops-engineer` | Write the code in their area |
| `pact-test-engineer`, `pact-qa-engineer`, `pact-security-engineer` | Test, run the app, review like an attacker |
| `pact-auditor` | Checks coders' work against the spec as they code |
| `pact-secretary` | Saves decisions and lessons; answers history questions |

By default, teammates run as plain background subagents, with no extra Claude Code setting. If you prefer Agent Teams, run `repact-init --teams` (or `/repacted:init --teams`): it turns on the [Agent Teams flag](../glossary.md#agent-teams-flag) and sets `REPACT_EXECUTION=teams`. Tasks, reports and acceptance work the same either way; `repact-doctor` prints the [execution mode](../glossary.md#execution-mode) in use. Where Claude Code cannot run Agent Teams (for example under `claude -p`), the session falls back to subagents and says so once.

## How a session starts

Start a session from your terminal with `repact work`, or open Claude Code as the lead and type your ask:

- `repact work "fix the parser on empty input"` starts a session on an ask; `repact work` with no ask takes the next row of the project's task log.
- `repact resume` prints where the last session stands and resumes it.

In a session, `/repacted:work`, `/repacted:inspect`, `/repacted:decide` and `/repacted:resume` do the same. The session's tasks are grouped under a team named like `session-1a2b3c4d`. With Agent Teams, the lead first runs [`/repacted:bootstrap`](../glossary.md#bootstrap) to find that team and show paused work.

## Two ways to give work

- `/repacted:work <ask>`: the lead sizes the ask and picks one of the two below for you.
- [`/repacted:comPACT`](../glossary.md#compact) `<domain> <task>`: one or more specialists on a small, self-contained task. There are no phases.
- [`/repacted:orchestrate`](../glossary.md#orchestrate) `<task>`: the full cycle. The [phases](../glossary.md#phase) `PREPARE`, `ARCHITECT`, `CODE` and `TEST` are tasks, and each one waits for the one before it.

## Example

You type:

```
/repacted:comPACT backend add a --verbose flag to the greet command
```

The lead runs [`repact dispatch`](../glossary.md#repact-dispatch), which makes one work task for a backend coder, a worktree and a brief, and prints the call that starts the coder. The coder says back the brief with `repact submit --teachback`; on a task this small it is accepted automatically ([teachback](teachback.md)). The coder adds the flag and a test, runs the tests with `tcount`, and hands in its [HANDOFF](handoff.md) with `repact submit`. The lead runs `repact accept`, which completes the task only if a recorded passing run tested the files as they are now.

## Messages

Agents talk with `SendMessage`. A busy teammate reads a message only when its turn ends. So anything that must arrive goes on the task file: a change to the brief is an amendment (`repact amend`), and `repact submit` refuses a HANDOFF that does not answer it. See [Task files and waits](task-files-and-waits.md).

## When work stops on you

`repact decide` (or `/repacted:decide`) lists every question that waits on you, numbered: task-log questions, unattended halts, parked tasks and held knowledge-base items. `repact inspect` shows where the work stands, and `repact inspect <task id>` prints what one task changed and why it was accepted.
