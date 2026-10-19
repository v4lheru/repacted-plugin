# Completion: accept, reject and amend (the lead)

The coder's side is in the teammate skill (`repact submit`).

## Completion authority

Only the lead closes a dispatched task, and only with `repact accept`. A coder hands in with `repact submit` and never completes its own task. `task_write_guard` refuses a raw `TaskUpdate` that completes a task carrying `metadata.dispatch`, and a write of a reserved key (`dispatch`, `teachback`, `handoff`, `handoff_rejection`, `acceptance`, `evidence_waiver`, `parked`, `last_op`, `revision_number`, `amend_<n>`) on any task; its refusal names the command to run instead. Your own todo, alert and plan tasks you still complete with `TaskUpdate`.

Each command checks its input, makes one task write and prints the next step in a few lines. If it stops half way, run the same command again: it finishes what is left and writes nothing twice. `repact inspect` lists any unfinished command.

| Command | What it does |
|---|---|
| `repact accept <id>` | A teachback waiting for you: stores your acceptance. A HANDOFF: runs the evidence check (a passing `tcount` run on the tree as it is now, red-first for a `fix` or `feature`), the acceptance criteria pinned at dispatch and the amendment dispositions. All pass: one write completes the task with `metadata.acceptance`, then the task log, journal and workflow state. Any fails: it stores the rejection itself (the facts are the reason), exits 1 and prints the line that resumes the coder. |
| `repact accept <id> --waive "<why>"` | Closes over failing checks, attended only. Unattended, it parks the task for a human instead (`repact decide` lists it) and exits 0. |
| `repact accept --feature <name> [--tree <path>]` | A feature with several tasks: every task's checks pass together on the merged tree (default `.worktrees/<name>`). Task-log rows close only then. |
| `repact reject <id> --reason "<text>"` or `--reason-file <f>` | Your judgement rejection of the stored HANDOFF: one write of `handoff_rejection` plus `revision_number` + 1, then the resume line with the reason. |
| `repact amend <id> --text "<text>" [--kind ruling\|correction\|premise\|addendum\|note\|scope\|halt]` | Any directive after dispatch, as the next `amend_<n>`. `--next` prints the key and writes nothing. |
| `repact inspect <id>` | The change receipt: what was asked, the diff, the runs and their inputs, waivers, known limits and what is left. `--key handoff\|teachback\|rejection\|amend_<n>` prints one stored object. |

Unattended, the third failing `accept` of one task parks it instead of rejecting again, so no unwatched loop runs on. A parked task waits for the owner; you cannot answer it by rejecting.

## Teachback review

A teachback is accepted automatically when the complexity total is below 10 and the coder answered `yes` on the complexity rationale. Otherwise it waits for you: read it with `repact inspect <id> --key teachback`, then compare it with the task as you dispatched it, for misstatements AND omissions of the goal, the constraints and the done criteria.

- Right: `repact accept <id>`.
- A misunderstanding: do not accept. `repact amend <id> --kind correction --text "<the correction>"`; the coder submits again.
- `no` or `concern` on the rationale: accept when the score stands (say why in an `amend --kind ruling`), or correct the scope of the work in the amendment. A withheld rationale (`WITHHELD: ignorance-dependent dispatch`) is accepted as it is: never write the withheld hypothesis into the task.
- At complexity 11+, `reasoning_reconstruction` checks self-consistency, not correctness: a false assumption makes its contingency the answer. Examples: [lead-reference.md](lead-reference.md#method-reconstruction-examples).

## What you still judge

The commands check facts. These stay yours, on every HANDOFF before you accept:

- **Does it do what was asked?** Read the receipt, not only the 4-line summary, when the change is high risk or the summary surprises you.
- **Uncertainty lines.** Each `uncertainty` item is fixed, or goes into the PR as a known limit. Never accept over one silently.
- **Scope.** A deliverable that predates a directive that crossed it is a no-fault ordering artefact: reject with the current scope, not as a misunderstanding.
- **Agreement at final gates.** Where no later teachback would catch a misreading, ask the specialist before you go on: "Confirming my understanding: [key decisions]. Correct?" Gates: TEST to PR (purpose: does it fulfil the original goal?), comPACT completion and plan-mode synthesis (procedure: does it match what was asked?). An unavailable specialist: note `Agreement: assumed, specialist unavailable` in the checkpoint.
- **The staged commit.** Before committing a coder's work, compare `git diff --cached --stat` with every open amendment. A mismatch means hold the commit and amend.
- **Three rejections** of one task is a blocker for `repact decide`, not a fourth rejection.

## Carve-outs

| Path | Rule |
|---|---|
| Signal tasks (`metadata.type` `blocker` or `algedonic`, `completion_type` `signal`) | The task is the signal; the emitter completes it. Auditor tasks carry `completion_type` `signal` with no `type` and complete themselves ([Concurrent Audit](coordination.md#concurrent-audit)). |
| The secretary (`pact-secretary`) | Completes its own briefing and memory-save tasks; no teachback. |
| Termination | An unrecoverable agent: `TaskStop`, then complete its task with `{"terminated": true, "reason": "..."}` and spawn fresh with the partial HANDOFF. |

## Agent Teams only

With plain subagents the resume line `accept`, `reject` and `amend` print is the whole delivery. Under Agent Teams the teammate waits in `repact-wait`, which wakes on the write itself:

- **Read-trigger precondition.** Read a stored object only after the teammate's notify arrives, with the digest it names (`repact inspect <id> --key handoff --digest <d>`). A digest mismatch is a data-integrity finding: surface it, never reject an acted-on submission for it. An empty read straight after the notify: read again after 1-2 s before you act.
- **Crossed-wake idles.** Every directive is on disk before its wake, so a wake that crosses an idle notice loses nothing. An idle notice is never evidence about a directive. On doubt, read the task file once. Never resend a value.
- **Directive-reflection check.** A message reaches a busy teammate only at its turn end, so a HANDOFF written in that turn cannot have seen it. That is why every directive is an `amend_<n>`: `amendment_relay` shows it to a busy teammate after its next tool call, and `repact submit` refuses a HANDOFF that leaves one undisposed.
