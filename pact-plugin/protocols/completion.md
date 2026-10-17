# Completion, teachback review and rejection (team-lead)

Teammate-side rules live in [teammate-handoff §On Completion](../skills/teammate/references/teammate-handoff.md#on-completion--handoff-required) and [§On Rejection](../skills/teammate/references/teammate-handoff.md#on-rejection-wake-signal-receipt).

## Completion Authority

You, the team-lead, are the **only** actor who marks a teammate-owned task `completed`. Teammates write the HANDOFF to `metadata.handoff`, idle on `intentional_wait{reason=awaiting_lead_completion}` and wait. The `TaskUpdate(status="completed")` flip is the load-bearing approval. Every directive is on the task file before its wake: acceptance is the `status`, a rejection is a `*_rejection` object, and anything else (a ruling, a correction, a commit confirmation, a HALT) is an `amend_<n>` key. The `SendMessage` is only the wake: one line with the task id and the digest, never a value. A teammate in `repact-wait` sees the write itself; the wake is for one whose turn ended, because `blockedBy` is pull-only and an idle teammate cannot self-wake.

### Acceptance — two-call atomic pair (BOTH required, `TaskUpdate` FIRST)

1. `TaskUpdate(taskId, status="completed")`: the status flip; auto-unblocks tasks with `blockedBy=[<id>]`.
2. `SendMessage(to="<teammate>", "[team-lead→<teammate>] Task #<id>: accepted on disk, digest <d>.", summary="Task accepted")`: the wake.

`TaskUpdate` FIRST, the `SendMessage` last. That write prints `HANDOFF on disk: rev N, digest <d>` or `NO HANDOFF ON DISK`: match both to the notify, then wake. If the send errors, retry it. Skipping it strands the teammate on `awaiting_lead_completion` until something else wakes them; `blockedBy` resolution is invisible without the wake.

### Rejection — two-call atomic pair (BOTH required, `TaskUpdate` FIRST)

1. `TaskUpdate(taskId, metadata={"teachback_rejection": {...}})` (Task A) or `{"handoff_rejection": {...}}` (Task B), shape per [Rejection Flow](#rejection-flow). A malformed object is returned (`NOT STORED`): rewrite it, send no wake.
2. Read the stored line, `Rejection stored on #<id> (owner <o>): revision <n>, digest <d>.`, then send the wake with its digest: `[team-lead→<teammate>] Task #<id>: rejected on disk, digest <d>.` The corrections stay on disk.

Same ordering and retry rule as acceptance. Rejection metadata leaves the teammate's `intentional_wait` set; the teammate CLEARs it on waking. **3+ rejection cycles** on one task is an imPACT META-BLOCK signal.

### Carve-outs

| Path | Trigger | Rule |
|---|---|---|
| Signal-tasks (predicate-witnessed) | `metadata.completion_type == "signal"` AND `metadata.type ∈ {"blocker", "algedonic"}` | The teammate self-completes: the task IS the signal. Auditor tasks carry `completion_type="signal"` with NO `metadata.type`; they self-complete as documented practice in [Concurrent Audit](coordination.md#concurrent-audit), not via the predicate. Do NOT add `metadata.type` to an auditor dispatch to make it fit. |
| Secretary briefing + memory-save (predicate-witnessed) | owner's team-config `agentType` ∈ `SELF_COMPLETE_EXEMPT_AGENT_TYPES` (`{pact-secretary}`) | The secretary self-completes its session briefing and memory-save tasks, whatever its spawn name. It stays alive afterwards. |
| imPACT termination (lead-driven, not predicate-witnessed) | `metadata.terminated == true` | You force-complete via `TaskStop` + `TaskUpdate(status="completed", metadata={"terminated": true, "reason": "..."})`. See [imPACT.md](../commands/imPACT.md). |

`is_self_complete_exempt(task, team_name)` in `shared/intentional_wait.py` witnesses only the first two rows; pass `team_name`. `task_lifecycle_gate.py` reads it advisory-only: a non-exempt self-completion gets a `self_completion` advisory and a `completion_disputed` writeback; no hook blocks. The secretary also has a separate dispatch carve-out (no TEACHBACK), keyed on the same `agentType`; see `agents/pact-orchestrator.md` §11 and `commands/bootstrap.md`.

### Accepting on the message

The teammate's notify `SendMessage` is the arrival signal: it carries the task id, the rev and the 12-hex digest its write's stored line printed, not the payload. `TaskGet` does NOT surface metadata. Read the one stored copy once, after the notify:

```
repact-payload {taskId} --digest {d}
```

It prints the stored HANDOFF (else the teachback) scrubbed, with rev and digest. Exit 0: the digest matches; accept or reject on what it printed. Exit 3 (`DIGEST MISMATCH`): the stored object changed after the notify, an integrity finding ([point 4](#read-trigger-precondition)); judge what it printed and ask the teammate to re-notify if the change is theirs. Exit 4: nothing stored; never accept it, ask for the write in an `amend_<n>` (kind `correction`). The HANDOFF fields are in `metadata.handoff` ([teammate §HANDOFF Format](../skills/teammate/references/teammate-handoff.md#handoff-format)).

**Acceptance flags.** Before accepting, read any acceptance flag the hook injected. A flagged claim needs evidence (a recorded run) or a rejection with the reason; never accept over a flag silently.

## Teachback Review

Every specialist dispatch creates **Task A** (`"<role>: TEACHBACK for <feature>"`) and **Task B** (`"<role>: <mission>"`, `blockedBy=[A]`), both owned by the teammate, with the rendered descriptions in `reference/dispatch/teachback-gated-dispatch.md`. Task A names Task B by subject pattern, never by id. Canonical sequence: `agents/pact-orchestrator.md` §11 and [Teachback-Gated Dispatch](../commands/orchestrate.md#teachback-gated-dispatch).

Read the teachback once after its notify arrives ([Read-Trigger Precondition](#read-trigger-precondition)), with the digest the notify names:

```
repact-payload {A_id} --key teachback --digest {d}
```

**Validate against the task as you dispatched it: check for misstatements AND omissions** of the objective, constraints and success criteria. On a misunderstanding, do not accept: reject with the correction ([Rejection Flow](#rejection-flow)). The teammate is blocked on Task A, so there is no proceed-race. This catches misunderstanding disguised as agreement before TEST.

### Validating Incoming Teachbacks

After the comparison, read `variety_acknowledgment.rationale_articulates_this_dispatch`: `"yes"` → accept; `"no"`/`"concern"` → re-stamp `metadata.variety` on Task B then accept, or write why the stamp stands as an `amend_<n>` (kind `ruling`) on Task A, then accept. Prefer orchestrator-side when the flag is correct. **EXCEPTION — a withheld stamp** (`WITHHELD: ignorance-dependent dispatch`): take NEITHER option; accept, stamp untouched. This overrides the preference for orchestrator-side correction: re-stamping writes the hypothesis the withholding exists to keep out of the task. The workflow behind it: [Variety](workflows.md#variety).

⚠️ A re-stamp RE-SENDS THE FULL `variety` OBJECT in ONE `TaskUpdate` (a partial one erases the omitted fields); read it back and enumerate the keys.

### Validating Method Reconstruction

`reasoning_reconstruction` checks self-consistency, not correctness; a false assumption makes its contingency the answer. Examples: `lead-reference.md` §Method Reconstruction Examples. What each ceremony must carry:

<!-- protocol:acceptance -->
Rendered from `hooks/shared/protocol_descriptor.py`:
- `teachback_submit`: an object with `understanding`, `most_likely_wrong`, `least_confident_item`, `first_action` (non-empty strings) and `variety_acknowledgment` (an object: `rationale_articulates_this_dispatch` in yes/no/concern, `concern` non-empty unless yes).
- `reasoning_reconstruction`: required when the variety total is 11+ (missing → `missing_reasoning_reconstruction`); not an object of exactly `decision_attribution`, `assumption_trace`, `contingency_clause` → `malformed_reasoning_reconstruction`; a sub-key that is missing, not a string or empty → `empty_reasoning_reconstruction_field`. Recommended at 7-10.
- `handoff`: `rev` a bare positive integer, 1 on the first write, +1 on each rewrite; `amendments` one disposition per `amend_<n>` (applied/declined/disputed or ack); required `produced`, `decisions`, `uncertainty`, `integration`, `open_questions`; `reasoning_chain` recommended; `refs` when your task carries project tasks.
- The wake, sent after the write is stored: `[team-lead→<teammate>] Task #<id>: <accepted|rejected|amended> on disk, digest <d>.` One line, the digest from the stored line, never a value; the teammate reads with `repact-payload <id> --key rejection` or `repact-payload <id> --key amend`.
<!-- /protocol -->

Optionally record `TaskUpdate(A_id, metadata={"teachback_resolution": {"conditions_met": true, "resolution_comment": "..."}})` first; it is not one of the required calls.

**Approve — two-call atomic pair, `TaskUpdate` FIRST:**

```
TaskUpdate(A_id, status="completed")
SendMessage(to="<teammate>",
    message="[team-lead→<teammate>] Task #<A_id>: accepted on disk, digest <d>.",
    summary="Teachback accepted; Task B claimable")
```

> ⚠️ Do NOT mark Task B `completed` or `pending`. It stays `pending` until the teammate claims it after the wake.

### Agreement Verification (Orchestrator-Side)

Teachback verifies understanding downstream (next agent → lead). Agreement verification checks it upstream (lead → previous agent), and only at **final gates**, where no downstream teachback would catch a misreading. Agreement levels, the depth of shared understanding: L0 topic (what), L1 procedure (how), L1.5 method (how, and why it stays true), L2 purpose (why).

| Gate | Level | Question |
|---|---|---|
| TEST → PR (orchestrate) | L2 (purpose) | "Does the implementation fulfill the original purpose?" |
| comPACT completion | L1 (procedure) | "Does the deliverable match what was requested?" |
| plan-mode synthesis | L1 (procedure) | "Does my synthesis accurately represent your input?" |

Flow: read the handoff, form your understanding, `SendMessage` the specialist "Confirming my understanding: [key decisions]. Correct?", then proceed on the confirmed understanding. It is a question, not a directive, so it stays a message. With concurrent specialists, confirm each one's piece with that specialist. If the specialist is already shut down or unresponsive, treat the handoff as accepted and note `Agreement: [assumed — specialist unavailable for verification]` in the checkpoint.

## Rejection Flow

Reject a teachback or HANDOFF on the task file FIRST, then send the one-line wake:

```
TaskUpdate(<A_id or B_id>, metadata={"<teachback|handoff>_rejection": {
    "reason": "<one-line summary>",
    "corrections": ["<correction 1>", "<correction 2>"],
    "since": "<canonical_since() output>",
    "revision_number": <the task's metadata.revision_number; 1 if unset>,
    "rev": <the handoff.rev you are rejecting>
}})
SendMessage(to="<teammate>",
    message="[team-lead→<teammate>] Task #<id>: rejected on disk, digest <d>.",
    summary="Rejected; revise")
```

- **Shape, checked on the write:** `reason` a non-empty string; `corrections` a list of non-empty strings, required; `since` a UTC time with its zone; `revision_number` as in the template: a lower one is refused (closed on arrival); `rev` required on `handoff_rejection` when the HANDOFF has a `rev`. A request for a missing `reasoning_reconstruction` is a line in `corrections`.
- **`rev` pins the rejection to the HANDOFF it judged:** it equals the `handoff.rev` you reviewed. A `rev` below the stored one is refused too: re-review the newer revision. One above is recorded (`rejection_rev_ok`).
- **Disk is the only copy:** the wake names the task and the digest, never the corrections. The teammate reads them with `repact-payload <id> --key rejection`; one inside `repact-wait` or mid-turn gets them from the file without the wake.
- **Recovery flow:** the teammate wakes, CLEARs the wait, revises (the teachback, or the deliverable plus the HANDOFF with `rev` +1), increments `metadata.revision_number`, sends the notify with the new rev and digest, and SETs a new wait (per the teammate skill §Waiting and waking). You then accept ([Completion Authority](#completion-authority)) or reject again.
- **Cycle limit:** 3+ rejection cycles on one task is an imPACT META-BLOCK signal ([imPACT.md](../commands/imPACT.md)).

## Crossed-Wake Idles

- Every directive is on disk before its wake, so a wake that crosses the teammate's turn-end idle notice loses nothing.
- An idle notice is never evidence about a directive, whichever was sent first.
- On doubt, read the task file once (raw JSON; `TaskGet` is metadata-blind): what it holds is the state.
- Never re-send a value: the task file holds it.

**Non-goal:** no synchronous hook detects a send (`SendMessage` fires no tool event; TeammateIdle fires at turn end regardless of the inbox); the `missed_wake_scan` hook is the backstop.

## Directive-Reflection Check

**Delivery is not processing.** Measured (277 lead messages in teammate transcripts, median delay 10.7 min): a message to a busy teammate arrives only at that teammate's turn end, never mid-turn. A teammate that ends its turn by writing the HANDOFF therefore cannot have seen a directive sent during that turn, and its "inbox empty" can be true.

- **Every post-dispatch directive is an `amend_<n>` top-level metadata key** on the task: `{"text": "...", "at": "<utc>", "from": "team-lead", "kind": "ruling|correction|premise|addendum|note|scope|commit|halt"}`, `n` the next free integer (`bin/repact-amend next <task>` prints the key, the object and the wake line; a commit confirmation is `--kind commit --sha <sha>`). Its write prints `Amendment amend_<n> stored on #<id> (owner <o>): digest <d>.`; the wake is `[team-lead→<teammate>] Task #<id>: amended on disk, digest <d>.` One key per amendment, never a list (`TaskUpdate` merges only top-level keys). A description change is an amendment too; the description stays the original brief.
- **Legacy keys** (readable, never written again): `lead_amendments`, `lead_amendment_2`, `lead_amendment_3`, `lead_rulings`, `lead_rulings_2`, `lead_rulings_3`, `lead_note`, `lead_directives_after_handoff`, `handoff_amendment`, `handoff_amendment_1`, `handoff_rejection_void`.
- **The wake is for an idle owner.** A busy in-process teammate gets the amendment as `LEAD AMENDMENT amend_<n>` context after its next tool call (the D2 relay hook), and any teammate reads it with `repact-payload <id> --key amend`; a message would sit until turn end.
- **The HANDOFF disposes every key** in `handoff.amendments` (the dispositions and the `rev` rule: the acceptance checklist above). Before acting on a boundary message, tick every amendment off against it. The D2 amendment gate checks the disposition on every HANDOFF write and prints a note naming an undisposed key; the write is stored (`modes.amendment_gate` in `bin/checks.json` ships as `note`).
- **The staged-work commit is the highest-stakes instance:** compare `git diff --cached --stat` against every outstanding amendment BEFORE committing; a mismatch means hold the commit and re-instruct.

A deliverable that predates a directive that crossed it is a no-fault ordering artifact: reject with corrections naming the current scope, not as a misunderstanding of the original dispatch.

## Read-Trigger Precondition

Before the raw JSON read is load-bearing, you MUST wait for teammate's wake-signal `SendMessage`:

1. **The notify `SendMessage` is the content-arrival signal.** The teammate sends it right after writing `metadata.teachback_submit` ([teachback Step 2](../skills/teammate/references/teammate-teachback.md#action-store-teachback-now)); a raw read before it arrives can see empty or stale metadata.
2. **The raw read follows the message, never precedes it.** Reversing the order produced false-empty reads and false rejection cycles.
3. **Residual race:** if the read finds nothing stored (exit 4) right after the message, re-read after 1-2 s. Do NOT reject on a single empty read.
4. **Digest check, disk vs notify.** The digest the notify names was computed from the task file after the write; `repact-payload --digest` recomputes it from the task file now. A match is the whole audit. A mismatch or a missing copy is a data-integrity finding whatever its origin (a later write, a lost write): record `metadata.integrity_finding` on the task and surface it; never reject an already-acted-on submission for it. The finding is advisory by design: no automated reader consumes it.

The same rules apply to the `metadata.handoff` read: wait for teammate's wake-signal `SendMessage` there too. The digest check of point 4 applies identically to the HANDOFF path. A teammate reads a rejection from the task file, with or without your wake.
