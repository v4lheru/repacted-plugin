# HANDOFF

A [HANDOFF](../glossary.md#handoff) is a teammate's final report on its work task. It is one object in `metadata.handoff` on the task file, so the lead reads exactly what the teammate stored, not a summary in a message.

## When to write it

Only when all of these hold:

- every edit is done
- the tests ran after the last edit, or the HANDOFF says why no test applies
- every acceptance point in the brief is met

## The keys

| Key | What it holds |
|---|---|
| `rev` | A whole number: 1 on the first write, one more on each rewrite. This is the [rev](../glossary.md#rev). |
| `amendments` | One entry for each `amend_<n>` on the task: `applied: <where>`, `declined: <why>`, `disputed: <why>` or `ack`. Use `{}` when there are none. |
| `produced` | The files and outputs the teammate made |
| `decisions` | The choices it made, and why |
| `reasoning_chain` | How the choices connect (recommended, not required) |
| `uncertainty` | What it is not sure of. Each [uncertainty item](../glossary.md#uncertainty) opens with `fixed:`, `known-limit:` or `not-applicable:`. |
| `integration` | What other parts of the system touch this work |
| `open_questions` | Anything the lead must decide. It can be an empty list. |

Test counts come from a recorded run. Run the tests with [`tcount`](../glossary.md#tcount) so the count you claim is on record.

## Example

```json
{
  "rev": 1,
  "amendments": {},
  "produced": ["greet.py: --verbose prints the name source to stderr", "tests/test_greet.py: one test"],
  "decisions": ["stderr, so piped output stays clean"],
  "reasoning_chain": "stderr because scripts pipe stdout, which keeps the old output unchanged",
  "uncertainty": ["not-applicable: no shared code touched"],
  "integration": ["none outside greet.py"],
  "open_questions": []
}
```

After the write, a check prints a [stored line](../glossary.md#stored-line):

```
HANDOFF stored on #13 (owner backend-coder): rev 1, digest 5c3ae4d70b21
```

If the check returns the write, nothing was stored: fix what it names and write the whole object again.

Then the teammate sends one line to the lead with the task id, rev and [digest](../glossary.md#digest), and waits.

## How the lead checks it

The lead reads the stored copy with the [payload reader](../glossary.md#repact-payload):

```
repact-payload 13 --digest 5c3ae4d70b21
```

It exits with 3 if the digest does not match. The lead then completes the task, or writes `metadata.handoff_rejection`. On a [rejection](../glossary.md#rejection), the teammate fixes the work and writes the HANDOFF again with `rev` 2.

## Checks you can tune

`bin/checks.json` sets how strict the write-time checks are. `handoff_evidence` (a test count no recorded run backs) ships as `advisory`: it adds a note but does not return the write. See the [configuration reference](../reference/config.md).
