# Lead and teammates

## The lead

The [lead](../glossary.md#lead) is the agent in your main Claude Code session. Its agent name is `repacted:pact-orchestrator`. The lead plans the work, splits it into [tasks](../glossary.md#task), hands each task to a teammate and checks every result. It edits code itself only for small, known changes. Everything else goes to a specialist.

The lead is also the only one who marks a task `completed`. A teammate never completes its own task.

You start the lead in one of two ways:

- `claude --agent repacted:pact-orchestrator`
- once per project, `/repacted:init --repo`, which adds `"agent": "repacted:pact-orchestrator"` to `.claude/settings.json`

## Teammates

A [teammate](../glossary.md#teammate) is an agent that the lead starts into its [team](../glossary.md#team). Each one runs in its own context, so it does not fill up the lead's context. Most teammates are [specialists](../glossary.md#specialist) with one job:

| Agent | Job |
|---|---|
| `pact-preparer` | Researches docs, APIs and best practices before design or code |
| `pact-architect` | Designs components, contracts and data flows |
| `pact-backend-coder`, `pact-frontend-coder`, `pact-database-engineer`, `pact-devops-engineer` | Write the code in their area |
| `pact-test-engineer`, `pact-qa-engineer`, `pact-security-engineer` | Test, run the app, review like an attacker |
| `pact-auditor` | Checks coders' work against the spec as they code |
| `pact-secretary` | Saves decisions and lessons; answers history questions |

Teammates need [full mode](../glossary.md#full-mode): the [Agent Teams flag](../glossary.md#agent-teams-flag) must be on.

## How a session starts

On your first prompt the lead runs [`/repacted:bootstrap`](../glossary.md#bootstrap). It finds the session's team (named like `session-1a2b3c4d`) and shows any paused work. Until bootstrap has run, a check refuses every attempt to start a teammate.

## Two ways to give work

- [`/repacted:comPACT`](../glossary.md#compact) `<domain> <task>`: one or more specialists on a small, self-contained task. There are no phases.
- [`/repacted:orchestrate`](../glossary.md#orchestrate) `<task>`: the full cycle. The [phases](../glossary.md#phase) `PREPARE`, `ARCHITECT`, `CODE` and `TEST` are tasks, and each one waits for the one before it.

## Example

You type:

```
/repacted:comPACT backend add a --verbose flag to the greet command
```

The lead creates two tasks for one backend coder: a [teachback](teachback.md) task and a work task. It starts the coder. The coder says back the brief, the lead accepts, the coder adds the flag and a test, and then writes a [HANDOFF](handoff.md). The lead reads the HANDOFF, checks the test count and completes the task.

## Messages

Agents talk with `SendMessage`. A busy teammate reads a message only when its turn ends. So the lead puts anything that must arrive on the task file instead. See [Task files and waits](task-files-and-waits.md).
