# HANDOFF (field reference)

Read this only when `repact submit` names a HANDOFF field. The brief's HANDOFF skeleton already holds every key; you replace the `<...>` values and fill the lists.

## On Completion — HANDOFF (Required)

Hand in when every edit is done, your tests ran after the last edit (with `tcount`, the way the brief's Tests line says) and every acceptance criterion in the brief has its entry in `refs`. Then run the brief's `repact submit <id>` line. A task whose result is a judgement rather than a change still gets a HANDOFF: its value is in `decisions` and `reasoning_chain`.

### HANDOFF Format

<!-- protocol:handoff-fields -->
| Key | Rule |
|---|---|
| `produced` | required: one item per file you changed, its path first, then what changed |
| `decisions` | a list, may be empty: the choices you made, one line each with its why |
| `reasoning_chain` | recommended: a few lines on how you got there |
| `uncertainty` | a list, may be empty: each item opens with `fixed:`, `known-limit:` (it goes into the PR) or `not-applicable:` |
| `integration` | required: what the lead or the next unit must know to use this |
| `open_questions` | a list, may be empty: what is still open; an unfinished step opens with `remaining:` |
| `amendments` | one entry per `amend_<n>` on the task, keyed by it: `applied: <where>`, `declined: <why>`, `disputed: <why>` or `ack`; `{}` with none |
| `refs` | one entry per ref or AC the skeleton lists: `done: <evidence>`, or `partial:`, `deferred:` or `dropped:` with the why |
| `rev` | do not set it: submit assigns it, one more than the stored one |

`repact submit` repairs what is mechanical: it sets `rev` itself, makes a relative `produced` path absolute against your worktree, and adds a missing `decisions`, `uncertainty`, `open_questions` as `[]`. It refuses, listing every problem at once and storing nothing:
- `produced` or `integration` missing;
- a `produced` path that resolves outside your worktree;
- an `uncertainty` item with none of `fixed:`, `known-limit:`, `not-applicable:`;
- an `amend_<n>` on the task with no entry in `amendments` (it prints the amendment's text);
- a listed ref or AC with no entry in `refs`, or an entry that opens with none of `done:`, `partial:`, `deferred:`, `dropped:`;
- a key that only looks like a canonical one, a `<...>` placeholder left from the skeleton, or a HANDOFF over 256 KB;
- a teachback that is not accepted yet.
<!-- /protocol -->

Keep it about ten lines of content: the lead reads a summary, not your transcript. The optional `evidence` object is described in the brief's Tests line.

## On Rejection (Wake-Signal Receipt)

`repact accept` turns failed facts into the rejection itself; the lead's `repact reject` adds a judgement reason. Either way a message names the task. Read it with `repact inspect <id> --key rejection`, fix every item, rerun `tcount` if code changed, and run the same submit line again: submit sets the next `rev`. An amendment works the same way (`repact inspect <id> --key amend_<n>`) and needs its entry in `amendments`.

If you could not finish, hand in what exists: each unfinished step goes in `open_questions`, opening `remaining:`.
