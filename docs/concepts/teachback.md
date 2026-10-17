# Teachback

A [teachback](../glossary.md#teachback) is a teammate saying the brief back to the lead before it starts. It catches a wrong reading of the task while the fix is still cheap.

## Two tasks per teammate

For each teammate, the lead creates a pair of tasks ([Task A and Task B](../glossary.md#task-a-b)):

- **Task A**, the teachback task. The teammate writes its teachback here.
- **Task B**, the work task. It is blocked by Task A, so it cannot start until the lead accepts.

The lead accepts by marking Task A `completed`. Then the teammate claims Task B and starts work.

## What a teachback holds

The teammate writes one object to `metadata.teachback_submit` on Task A, with five fields:

| Field | What it says |
|---|---|
| `understanding` | The brief in the teammate's own words: what it builds, the limits, the interfaces |
| `most_likely_wrong` | The part of its reading most likely to be wrong |
| `least_confident_item` | One assumption the lead should confirm |
| `first_action` | The first thing it will do |
| `variety_acknowledgment` | Whether the lead's [variety](../glossary.md#variety) scores fit this task: `yes`, `no` or `concern`, with a reason unless `yes` |

The work task carries the lead's variety scores in `metadata.variety`: novelty, scope, uncertainty and risk, each from 1 to 4, and their total. When the total is 11 or more, the teachback must also hold `reasoning_reconstruction`, with three keys: `decision_attribution`, `assumption_trace` and `contingency_clause`.

## Example

A teammate is asked to add a `--verbose` flag to a toy CLI. It writes:

```json
{
  "understanding": "Add --verbose to the greet command. It prints the resolved name and greeting source to stderr. No change to normal output. One test.",
  "most_likely_wrong": "Whether --verbose output goes to stderr or stdout.",
  "least_confident_item": "Please confirm stderr is right.",
  "first_action": "Read greet.py and its test file.",
  "variety_acknowledgment": {"rationale_articulates_this_dispatch": "yes", "concern": ""}
}
```

A check reads the write and prints a [stored line](../glossary.md#stored-line):

```
Teachback stored on #12 (owner backend-coder), digest 0dd58d248e04.
```

Only after that line does the teammate tell the lead. Its message names the task id and the [digest](../glossary.md#digest), not the teachback itself. The lead reads the stored copy. Then the teammate sets a [wait marker](../glossary.md#wait-marker) and waits with [`repact-wait`](task-files-and-waits.md).

## Rejection

If the reading is wrong, the lead writes `metadata.teachback_rejection` on Task A instead of completing it. That write wakes the teammate. It reads the [rejection](../glossary.md#rejection) with `repact-payload <id> --key rejection`. Then it writes the whole teachback again with `metadata.revision_number` set to 2, and tells the lead again.

## Rules to remember

- No code edits before the teachback is stored.
- The teammate never completes Task A. The lead does.
