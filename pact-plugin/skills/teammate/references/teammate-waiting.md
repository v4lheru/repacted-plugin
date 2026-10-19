# Waiting, idling and waking (Teams mode only)

**Teams mode only.** This page applies only when you run as an Agent Teams teammate (your spawn context names a team). In the default subagent mode none of it applies: you end your turn, and the lead resumes you with a message.

In Teams mode, after each `repact submit` you send the team-lead one line, SET the wait below and run `repact-wait` in the foreground. Read the rest of this file when a wait outlasts 30 minutes, when work must outlive your turn, or when a wake and the disk disagree.

## The wait, in short

<!-- protocol:wait -->
1. SET before idling on any protocol wait, as a top-level key: `TaskUpdate(taskId, metadata={"intentional_wait": {"reason": "awaiting_lead_completion", "expected_resolver": "lead", "since": "<now>", "covers_since": "<now>"}})`.
2. `reason` and `expected_resolver` are non-empty strings. Resolvers: `external`, `lead`, `peer`, `user`. Other reasons: `awaiting_amendment_review`, `awaiting_blocker_resolution`, `awaiting_lead_commit`, `awaiting_lead_takeover`, `awaiting_peer_response`, `awaiting_post_handoff_decision`, `awaiting_teachback_approved`, `awaiting_user_decision`.
3. `since` is a tz-aware UTC clock reading, never a guess: run `date -u +%Y-%m-%dT%H:%M:%S+00:00` alone; only it and `repact-wait` may run before acceptance.
4. `covers_since` equals `since` on a new wait; a re-SET of a wait you still hold takes a fresh `since` and carries `covers_since` unchanged: the write replaces the whole object.
<!-- /protocol -->
5. CLEAR on the turn the wait resolves: `TaskUpdate(taskId, metadata={"intentional_wait": null})`.
6. Stay in the turn: run `repact-wait <id> --until completed,rejection,amend` in the FOREGROUND (Bash timeout 600000). HANDOFF accepted: run plain `repact-wait <task id>` (no `--until`) once more; the lead stops you.
7. After a turn end the lead's `SendMessage` wakes you; never poll `TaskList` or make filler calls (`true`, `sleep`). Run long commands in the foreground, never `run_in_background`: a turn end over a background job is refused unless a wait names the job (`covers_since`).

## Idle Discipline

When you wake with no new work, return to idle silently — no "standing by" or
"still waiting" acknowledgments. The idle state is the message-delivery channel;
output (even zero-content) blocks the next inbox delivery.

- **No new `SendMessage` and no new dispatch instructions?** Do not emit.
- **Idle-waiting for a protocol-defined resolution** (teachback, team-lead commit,
  peer reply, user decision)? Use the `intentional_wait` task metadata per
  the Intentional Waiting section below.
- **Awaiting lead completion?** SET the wait (`intentional_wait`; its shape and vocabulary are in §The wait, in short) after `repact submit` stored your HANDOFF or teachback AND you sent the one-line `SendMessage` to the team-lead. **Ordering invariant** (audit anchor, lead-side mirror): `repact submit` FIRST → one-line `SendMessage` SECOND → intentional_wait SET THIRD. This ordering exists because the team-lead must wait for teammate's wake-signal `SendMessage` before treating their raw task-file read as authoritative — see [completion.md §Read-Trigger Precondition](../../../protocols/completion.md#agent-teams-only). Sending the `SendMessage` before the metadata write lands produces false-empty raw reads on the lead side; going idle before the `SendMessage` strands the lead silently. Then, FOURTH, do not end the turn: run `repact-wait <task id>` in the foreground ([§Waiting in the Turn](#waiting-in-the-turn-repact-wait)). Once your turn has ended, do NOT poll `TaskList` while idle — you cannot self-wake to do so; the team-lead's wake-signal `SendMessage` is the resolver. Your notify carries only the task id, rev and digest; the lead reads the stored copy once, after it arrives.
- **Genuinely stuck**? Follow the On Blocker section.

If you have nothing to say that advances the work, say nothing.

**Outbound direction**: a `SendMessage` you send lands in the recipient's
inbox at their next idle boundary, not instantaneously. See
[Communication Charter Part I — Teammate-Side Discipline — Verify Before Acting + Assume Eventually-Seen](teammate-messaging.md#teammate-side-discipline--verify-before-acting--assume-eventually-seen)
for verify-before-acting and assume-eventually-seen rules that follow from
this delivery model.

### Counter-Confirm Suppression

Before sending ANY "crossed messages", "already done", "still awaiting", or similar
state-clarification message, you MUST take a fresh disk read of the referenced task
(`status`, `blockedBy`, relevant metadata). If durable state already shows the
situation resolved — the task you would claim attention for is `completed`, the
approval or commit you would report as pending is already recorded — send NOTHING.
Durable state IS the reply: the team-lead reads the same disk. A clarification
composed from a disk read that predates the team-lead's resolution writes asserts
state that is false on arrival — pure noise that can seed a politeness loop in both
directions.

This is the already-resolved specialization of the charter's
[Verify Before Executing](teammate-messaging.md#verify-before-executing)
rule: "no-op and report" still applies when the fresh read shows state has diverged
in a way the team-lead must act on; when the fresh read shows exactly the state the
team-lead themselves resolved, the report carries zero information — suppress it.

This suppression rule and [§On Wake: Disk-First Re-Read](#on-wake-disk-first-re-read-seam-agnostic)
are complements, not substitutes: disk-first governs how you INTERPRET an inbound
wake; suppression stops stale OUTBOUND noise. Applying one does not discharge
the other.

## Intentional Waiting

When your task is `in_progress` but you are legitimately idle awaiting a message
(teachback approval, inter-commit hold, peer reply, user decision, blocker
resolution), signal it via the `intentional_wait` task metadata BEFORE going idle.
This flag has a lead-side consumer: the `missed_wake_scan` hook re-surfaces tasks
idling on `awaiting_lead_completion` past the staleness threshold at the start of
a team-lead turn opened by a user prompt, a scheduled wake or a background-task
notification, and at session start — not on a turn opened by a teammate message.
The schema primitives
(`KNOWN_REASONS`, `KNOWN_RESOLVERS`, `wait_stale`) in `shared.intentional_wait`
define the teammate-facing metadata contract for protocol-defined waits. Using the flag documents the wait intent for the team-lead's task-file
inspection and for post-hoc session review.

The waits above are **protocol waits**: a named resolver drives completion and
you idle until they wake you. **Self-started work** — your own verification
gates, builds, long-running commands — has no external resolver; nobody but
you is watching it. The discipline for that class is the three layers below.

### Wait Discipline for Self-Started Work

**Layer 1 — In-turn when it fits.** Run a long command as a normal foreground
call whenever it fits the `Bash` tool timeout (declared max 600000 ms). Most
verifications finish in seconds; run them in-turn and the wait never exists as
a turn boundary. The full suite too: do not `run_in_background` it. As an
in-process teammate you never receive a background job's completion notice, and
the lead may not either (in testing, most were never delivered), so
you would idle until the lead's 30-minute missed-wake scan. Test runs are the
measured case: several units idled on finished background runs, one for
almost five hours, each with its wait set.

**Layer 2 — Never hold an unflagged dependency.** Before ending ANY turn whose
deliverable depends on unfinished work, SET `intentional_wait{reason,
expected_resolver, since, covers_since}` per the SET subsection below — the dead-man's handle
that makes a stalled watcher detectable instead of silent. This is
unconditional: it does not depend on any wake channel.

**Layer 3 — Escalate what you cannot hold.** Work that genuinely exceeds the
timeout must not sit invisibly in a backgrounded process. Do not background it
and end the turn relying on the completion notification to re-invoke you: when
you run in-process it surfaces only when something else starts your next turn,
so the team-lead's channel is the only push you can count on.
Either split the work into timeout-sized chunks run in-turn, or transfer the
watch explicitly: stage the current state, `SendMessage` the team-lead the
pending-work description, and flag the wait with `expected_resolver=lead`.
Set a free-form `reason` that names the transferred watch (e.g.
`awaiting_lead_takeover`), not `awaiting_lead_completion` — that reason names
the HANDOFF/teachback acceptance wait, and a transferred watch must read as
the different wait it is on task-file inspection.

Silence is uninformative in both directions, and narrating a wait is noise in
both. Do not emit "still running" or "waiting on the gate" turns while your own
work runs, and do not reply to a turn that carries no actionable content. That
reply rule is the wait-context instance of [§Idle Discipline](#idle-discipline)'s
say-nothing rule. A
bare filler call (`true`, `sleep <N>`) manufactures the next turn without
producing new information — the `wait_filler_gate` hook denies exactly these —
so if the turn has nothing to advance, end it with no tool call at all.

### Waiting in the Turn: `repact-wait`

Every teammate turn end sends the lead an idle notice, and each notice is a full lead turn on a large context (in one field session about a third of the lead's turns were idle-only). A teammate that waits inside its turn sends none. So after the notify and the SET, run:

```
repact-wait <task id>        # foreground, Bash timeout 600000
```

It polls the task file every 2 s and returns when the file changes or disappears (default timeout 560 s). It prints one line: the time, the status, `blockedBy`, and the metadata keys added, changed or removed, never a value. The team is `--team`, else this session's team that holds the task; two such teams or none exits 2 naming the teams that hold the id, so pass `--team`.

| Exit | Meaning | Do |
|---|---|---|
| 0 | the task file changed | Re-read the task file and act: `completed` (CLEAR the wait; nothing more after an accepted HANDOFF), a `teachback` acceptance (CLEAR, then go on with the work), `*_rejection` (CLEAR, then §On Rejection), `amend_<n>` (dispose of it). On a rejection or an amendment a second line names the reader: `next: repact inspect <id> --key rejection` (or `--key amend`). Nothing to act on (your own or a hook's write): run it again. |
| 3 | timeout, no change | Run it again. After 3 in a row, end the turn: the wait you SET stays, so `missed_wake_scan` and the stall checks still see you. |
| 4 | task file missing or gone | Report it to the team-lead, then end the turn. |
| 2 | usage, or team not resolved | Rerun with `--team`. |

A `SendMessage` sent to you during the wait is not delivered until your turn ends (measured live). Everything you act on must be on disk, which the protocol already guarantees: acceptance is the status, a rejection is `metadata.*_rejection`, an amendment is an `amend_<n>` key. A message never carries a value: read a rejection with `repact inspect <id> --key rejection` and a ruling with `--key amend`.

SET the wait before the first run and leave it through every rerun: a re-SET writes the file and wakes your own wait. After the lead accepts your HANDOFF, run `repact-wait` once more and do nothing else: the lead stops you mid-wait, which sends no idle notice. A stop mid-command is expected, not an error.

### SET — before going idle

```python
from datetime import datetime, timezone
now = datetime.now(timezone.utc).isoformat(timespec="seconds")
TaskUpdate(taskId=taskId, metadata={
    "intentional_wait": {
        "reason": "awaiting_teachback_approved",
        "expected_resolver": "lead",
        "since": now,
        "covers_since": now,
    }
})
```

`since` must be tz-aware ISO-8601. A naive timestamp fails `validate_wait` and will be surfaced as malformed to any reader of the flag (team-lead inspection, audit, future consumers). Fail-loud.

### CLEAR — when the wait resolves

```python
TaskUpdate(taskId=taskId, metadata={"intentional_wait": None})
```

Clear on the same turn you take the action that advances state (e.g., when the approval / commit confirmation / peer reply / user decision arrives). The commit confirmation is an `amend_<n>` of kind `commit` carrying the `sha`, or the completion of the task when the commit ends it.

### On Wake: Disk-First Re-Read (Seam-Agnostic)

This rule fires on EVERY wake while you hold ANY `intentional_wait` — every reason
(`awaiting_lead_completion`, `awaiting_lead_commit`, `awaiting_teachback_approved`,
and all others), every seam — and, more generally, whenever ANY inbound directive
could have crossed a turn you had in flight. It applies identically under in-process
and tmux teammateMode: the race is message-delivery ordering, not mode-specific.
Inbox delivery is asynchronous, so a wake can trail the durable write it
names, arrive after an unrelated message, or name a scope your in-flight
work predates. There is one class of wake: every wake is a signal that the task file
changed. It names the task and a digest (`[team-lead→<you>] Task #<id>:
<accepted|rejected|amended> on disk, digest <d>.`) and never carries a value.

1. **Re-read durable state FIRST**, before acting on a wake — `status`,
   `blockedBy`, current description, and the relevant metadata keys
   (`teachback_rejection`, `handoff_rejection`, `amend_<n>`, or whichever key your
   wait names). Read a value with `repact inspect <id> --key rejection` or
   `--key amend`: its header prints the digest the wake names. `TaskGet` does not
   surface metadata.
2. **Durable state is authoritative; the wake is only a signal.** If durable state
   shows your wait resolved — the gate task `completed`, a commit confirmation (an
   `amend_<n>` of kind `commit`, or implied by task state), a rejection record
   present — CLEAR the wait and proceed immediately, even if no wake message
   is visible yet. Do not wait for the message that durable state has already
   made redundant.
3. **If durable state shows the wait unresolved, keep waiting.** A wake that
   resolves nothing (a crossed or redundant message, a peer ping) gets no reply —
   return to idle silently per §Idle Discipline and §Counter-Confirm Suppression.
   If the wake names a write the disk does not yet show (e.g., the wake says
   "rejected on disk" but `repact inspect --key` exits 4), the durable write may
   still be in flight: re-read once after a brief pause; never act on a single empty
   read; if still empty, keep waiting.
4. **Crossed mid-turn directives reconcile the same way.** If an inbound directive
   (a scope change, a correction) could have crossed work you had in flight — your
   teachback or HANDOFF was being composed when it was sent — re-read the task's
   CURRENT description and metadata from disk and act on the current state, not on
   the state your in-flight work assumed. If your already-submitted deliverable
   reflects the pre-directive scope, revise it on the same task without waiting to
   be asked.

This rule generalizes the read flow of §On Rejection to every wait
resolution, and it is what makes the team-lead's wake message redundant-by-design:
ordering-immune at every seam, with no hook required (a synchronous wake-detection
hook cannot exist — see the non-goal note in
[completion.md §Crossed-Wake Idles](../../../protocols/completion.md#agent-teams-only)).
The no-poll discipline is unchanged: you still cannot poll while idle; this rule
fires ON wake, whatever woke you.

### Vocabulary

| Field | Required | Accepted values |
|-------|----------|-----------------|
| `reason` | yes | Non-empty string. Prefer `KNOWN_REASONS` from `shared.intentional_wait`: `awaiting_teachback_approved`, `awaiting_lead_commit`, `awaiting_amendment_review`, `awaiting_post_handoff_decision`, `awaiting_peer_response`, `awaiting_user_decision`, `awaiting_blocker_resolution`, `awaiting_lead_takeover`. Free-form permitted. |
| `expected_resolver` | yes | Non-empty string. Prefer `KNOWN_RESOLVERS`: `lead`, `peer`, `user`, `external`. Free-form permitted. |
| `since` | yes | tz-aware ISO-8601 UTC timestamp, seconds precision. |
| `covers_since` | on every SET | tz-aware ISO-8601 UTC timestamp. On a SET that starts a wait (the first SET, or any SET after a CLEAR), write the same value as `since`. When you re-SET a wait you are still holding, write `covers_since` again with its existing value in the same `TaskUpdate` — the write replaces the whole wait object, so leaving the field out deletes it. If it is already missing, write the value `since` held BEFORE you overwrite it. |

Unknown keys are preserved (forward-compat).

### Staleness safeguard

The `wait_stale` primitive in `shared.intentional_wait` considers the flag stale after 30
minutes from `since`. The `missed_wake_scan` hook surfaces `awaiting_lead_completion` waits stale past
this threshold to the team-lead. It also surfaces a wait with `expected_resolver` `peer` once two or
more owners are each past 30 minutes from `covers_since` (or from `since` when `covers_since` is absent or invalid), and a wait with no valid `covers_since` that
covers a background launch. And it names, once per run, a teammate holding a wait while a test run from its worktree (the
task's `Worktree:` line, else the linked worktree its own calls ran in) with no foreground caller
ended after the wait was raised; that reaches the lead only on its next prompt, so it is a backstop, not
a wake. No hook surfaces any other stale wait; the team-lead may inspect it by
reading the task file. If your wait genuinely takes longer, re-SET with a fresh `since` so
later inspection reflects the real duration.

**When you re-SET a wait you are still holding, give it a fresh `since` and carry `covers_since` forward unchanged in the same write.** `since` is the freshness clock and `covers_since` is the scoping anchor; they are two jobs and re-stamping must move only the first. The anchor is what decides which background launches your wait already acknowledged, so carrying it forward unchanged is what keeps a long wait from silently acquiring launches you started after raising it. Write the whole wait object in one `TaskUpdate`, `covers_since` included — a write that omits the field deletes it. If the wait already has no `covers_since`, write the value `since` held before you overwrite it. A wait you SET after a CLEAR is a new wait: write `covers_since` equal to its new `since`.

### When NOT to set

- **Consultant mode** (no owned `in_progress` task) with nothing outstanding. If you background work as a consultant, SET the wait on your most recently completed task.
- **Waits < 30 seconds**: SET+CLEAR bookkeeping isn't worth it for brief waits.
- **Completion gating**: the flag does NOT suppress the team-lead's HANDOFF acceptance check — an empty or missing `metadata.handoff` is flagged there regardless of intentional_wait state. Store your HANDOFF before you notify the team-lead.

## Consultant Mode

When your active task is done and no follow-up tasks are available:
- You are a **consultant** — remain available for questions
- Respond to `SendMessage` questions from other teammates
- Do NOT seek new work outside your domain
- Do NOT proactively message unless you spot a problem relevant to active work
