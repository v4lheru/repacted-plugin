# HANDOFF

A [HANDOFF](../glossary.md#handoff) is a teammate's final report on its work task. It is stored as one object in `metadata.handoff` on the task file, so the lead judges exactly what the teammate stored, not a summary in a message.

## When to hand it in

Only when all of these hold:

- every edit is done
- the tests ran with [`tcount`](../glossary.md#tcount) after the last edit, or the HANDOFF says why no test applies
- every acceptance point in the brief is met

The brief names a HANDOFF file that already holds every key. The teammate fills it in and runs the command its brief gives:

```
repact submit <id> --as <name>
```

## The keys

| Key | What it holds |
|---|---|
| `amendments` | One entry for each `amend_<n>` on the task: `applied: <where>`, `declined: <why>`, `disputed: <why>` or `ack`. Use `{}` when there are none. |
| `produced` | The files and outputs the teammate made |
| `decisions` | The choices it made, and why |
| `reasoning_chain` | How the choices connect (recommended, not required) |
| `uncertainty` | What it is not sure of. Each [uncertainty item](../glossary.md#uncertainty) opens with `fixed:`, `known-limit:` or `not-applicable:`. |
| `integration` | What other parts of the system touch this work |
| `open_questions` | Anything the lead must decide. It can be an empty list. |

`repact submit` sets the [rev](../glossary.md#rev) itself: 1 on the first HANDOFF, one more on each new one. It also makes a relative path in `produced` absolute and turns a missing list into `[]`. It refuses, listing every problem at once, when `produced` or `integration` is missing, an uncertainty item has no prefix, or an amendment on the task has no entry.

Do not type test counts into the HANDOFF. The lead's check reads the recorded runs.

## Example

```json
{
  "amendments": {},
  "produced": ["greet.py: --verbose prints the name source to stderr", "tests/test_greet.py: one test"],
  "decisions": ["stderr, so piped output stays clean"],
  "reasoning_chain": "stderr because scripts pipe stdout, which keeps the old output unchanged",
  "uncertainty": ["not-applicable: no shared code touched"],
  "integration": ["none outside greet.py"],
  "open_questions": []
}
```

```
repact submit 12 --as coder-1
HANDOFF stored on #12: rev 1 (...).
accept will pass the checks it can see now. End your turn.
```

After storing it, `repact submit` previews the lead's check. If the check would fail, it says why and exits with 3, for example `accept will refuse:` followed by `no passing tcount run since dispatch in the worktree or on its current inputs`. The teammate fixes that and submits again while the work is still fresh.

## How the lead checks it

The lead runs `repact accept <id>`. It accepts only on facts:

- **A passing run on the files as they are now.** There must be a recorded passing `tcount` run whose input files match the worktree now. A change to any input file after the run makes the run stale. Markdown files, ignored files and secrets are not inputs.
- **Red first for fixes and new behaviour.** A task dispatched as a fix or a feature also needs a failing run before the passing one, over at least one of the same test files.
- **Pinned acceptance criteria.** When the lead pinned a test command for a criterion, only a run with the same test selection meets it.

When every check passes, the task is completed and its known limits and decisions go into the project's record. When a check fails, `repact accept` writes the [rejection](../glossary.md#rejection) itself, with the problems listed, and prints the message that sends the teammate back. The lead can also reject on judgement with `repact reject <id> --reason "..."`. The teammate reads a rejection with `repact inspect <id> --key rejection`, fixes the work, and submits again.

`repact inspect <id>` prints the task's change receipt: what was asked, the teachback, the diff, the test runs and whether they tested the current files, waivers, known limits, decisions, amendments and what is left.

## Closing over a failing check

Attended, the lead can close a task over failing checks with `repact accept <id> --waive "<why>"`; the receipt shows the waiver at the top. In unattended work a waiver never closes anything: it parks the task for you in [`repact decide`](../glossary.md#repact-decide).

## Checks you can tune

`bin/checks.json` in the installed plugin sets the check modes. `handoff_evidence` ships as `gate`: `repact accept` refuses while the evidence is missing or stale. `advisory` turns each problem into a note, and `off` runs nothing. See the [configuration reference](../reference/config.md).
