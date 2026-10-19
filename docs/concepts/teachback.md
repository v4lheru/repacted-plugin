# Teachback

A [teachback](../glossary.md#teachback) is a teammate saying the brief back to the lead before it starts. It catches a wrong reading of the task while the fix is still cheap.

## One task, two steps

The lead makes one work task per teammate with `repact dispatch`. The teachback is stored on that same task, before the work. There is no separate teachback task.

The brief the teammate gets names two files and the exact commands to run:

1. **Teachback.** It fills in the teachback file and runs `repact submit <id> --teachback <file>`.
2. **Work, then report.** It does the work and hands in its [HANDOFF](handoff.md) with `repact submit <id>`.

## Accepted automatically, or by the lead

The work task carries the lead's [variety](../glossary.md#variety) scores: novelty, scope, uncertainty and risk, each from 1 to 4, and their total.

- **Total below 10, and the teammate answers `yes`** (the scores fit the task): `repact submit` accepts the teachback on the spot and prints `Accepted automatically. Go on with the work.`
- **Total 10 or more, or the teammate answers `concern` or `no`**: the teachback waits for the lead. The teammate stops and ends its turn with the one line `TEACHBACK SUBMITTED #<id>`. The lead reads it with `repact inspect <id> --key teachback` and accepts it with `repact accept <id>`, which prints the message that resumes the teammate.

This works the same with plain subagents (the default) and with [Agent Teams](../glossary.md#agent-teams-flag). With Agent Teams, the waiting teammate runs [`repact-wait`](task-files-and-waits.md) instead of ending its turn, and the lead's accept wakes it.

## What a teachback holds

One JSON object with five fields:

| Field | What it says |
|---|---|
| `understanding` | The brief in the teammate's own words: what it builds, the limits, the interfaces |
| `most_likely_wrong` | The part of its reading most likely to be wrong |
| `least_confident_item` | One assumption the lead should confirm |
| `first_action` | The first thing it will do |
| `variety_acknowledgment` | Whether the lead's scores fit this task: `yes`, `no` or `concern`, with a reason unless `yes` |

When the variety total is 11 or more, the teachback must also hold `reasoning_reconstruction`, with three keys: `decision_attribution`, `assumption_trace` and `contingency_clause`.

`repact submit` checks the object and lists every problem in one message. Nothing is stored until it passes.

## Example

A teammate is asked to add a `--verbose` flag to a toy CLI. The task's variety total is 6. It fills in its teachback file:

```json
{
  "understanding": "Add --verbose to the greet command. It prints the resolved name and greeting source to stderr. No change to normal output. One test.",
  "most_likely_wrong": "Whether --verbose output goes to stderr or stdout.",
  "least_confident_item": "Please confirm stderr is right.",
  "first_action": "Read greet.py and its test file.",
  "variety_acknowledgment": {"rationale_articulates_this_dispatch": "yes", "concern": ""}
}
```

Then it runs the command from its brief:

```
repact submit 12 --as coder-1 --teachback briefs/12.teachback.json
Teachback stored on #12. Accepted automatically. Go on with the work.
```

## Correcting a teachback

The lead does not reject a teachback. If the reading is wrong, it writes a correction on the task with `repact amend <id> --kind correction --text "..."` and sends it to the teammate. The teammate fixes its teachback file and runs `repact submit <id> --teachback <file>` again.

## Rules to remember

- No code edits before the teachback is stored.
- The teammate never accepts its own teachback or completes its own task. `repact accept` is the lead's command.
