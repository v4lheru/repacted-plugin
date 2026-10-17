# HANDOFF, rejection and revision (full reference)

Not on the happy path: the checklist in [SKILL.md](../SKILL.md#handoff) is enough to write a HANDOFF. Read this file when a gate returns your HANDOFF write, when a rejection wakes you, or when you add the optional `evidence` object.

## On Completion — HANDOFF (Required)

When your work is done, you store the HANDOFF and remain `in_progress`. **You do NOT mark your own tasks `completed`** — the team-lead is the authoritative completion signal.

**Step 0 — Verification precondition (MUST pass before Step 1).** Do NOT begin the HANDOFF write sequence until ALL of the following are true:

- All file edits for the task are complete.
- All deliverables are staged via `git add`. (`git status` shows your intended changes in "Changes to be committed".)
- All relevant tests have been run with exit code 0. If no tests apply to your change, you MUST state "no tests applicable" with one-line reasoning in your HANDOFF.
- The deliverable matches every acceptance criterion in the task description. Tick each one off explicitly before proceeding.
- You re-read the task file and gave every `amend_<n>` key on it a disposition in `amendments` (see [Amendments and rev](#amendments-and-rev)); a directive relayed mid-turn as `LEAD AMENDMENT amend_<n>` context counts too.

If ANY precondition is unmet, KEEP WORKING. Do not write `metadata.handoff` to "reserve a spot" or "draft the handoff while tests run." The handoff metadata write is a commitment that the work IS done, NOT a wrap-up artifact you build in parallel with finishing.

> **Ordering invariant** (audit anchor): the three steps below MUST execute in the order Step 1 → Step 2 → Step 3 — `metadata.handoff` write FIRST, then notify `SendMessage` to team-lead, then `intentional_wait` SET. This ordering is load-bearing for the team-lead's [Read-Trigger Precondition](../../../protocols/completion.md#read-trigger-precondition): the lead must wait for teammate's wake-signal `SendMessage` before treating the raw `cat "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/tasks/.../{taskId}.json" | jq .metadata.handoff` read as authoritative, but the `SendMessage` is only safe to send AFTER the metadata write has landed on disk. The write persists the one copy the lead, the harvest and the recovery paths read; the notify carries only the task id, rev and digest, and the lead reads that copy once with `repact-payload`, the digest proving it is the copy you stored. Reversing Step 1 and Step 2 produces false-empty raw reads on the lead side that have triggered false-positive HANDOFF rejection cycles. Reversing Step 2 and Step 3 (idle before `SendMessage`) silently strands the lead — they will never see the wake-signal because you went idle without sending it. Editors of this skill: do NOT re-order these steps.

1. **Store HANDOFF in task metadata**:
   ```
   TaskUpdate(taskId, metadata={"handoff": {
     "rev": 1,                  // 1 on the first write, +1 on each revision
     "amendments": {"amend_1": "applied: <where>"},  // every amend_<n> key; {} if none
     "produced": [...],
     "decisions": [...],
     "reasoning_chain": "...",  // recommended — include unless task is trivial
     "uncertainty": [...],      // each item opens with its disposition (below); [] if none
     "integration": [...],
     "open_questions": [...],
     "evidence": {...},         // optional; builders, see below
     "refs": {...}              // required when the task carries project tasks, see below
   }})
   ```
   **`refs`: the project tasks your task carries.** When the lead bound project tasks to your task (`metadata.project_task`, `metadata.refs`, or a `ptask:<team>/<id>` link in the task log, written by `repact-task start <ref> --ptask`), dispose of each in `handoff.refs`: `"done: <evidence>"` (or just `"done"`), `"partial: <what is left>"`, `"deferred: <why>"` or `"dropped: <why>"`. Example: `"refs": {"T-<id>": "done: the fix and its test", "T-<id2>": "partial: the owner's config step is left"}`. The HANDOFF gate returns a write that misses a bound ref; the lead's acceptance closes the `done` refs on your `evidence` and reports the rest. A ref only named in the task's text gets a note, not a return; dispose of it anyway when you know its state.
   **The optional `evidence` object (builders).** When your work produced commits or a test run, add `"evidence": {"branch": "<branch>", "head": "<sha>", "merge": "<sha, once merged>", "tag": "<tag>", "tests": [{"log": "<bare log file name>", "collected": 0, "passed": 0, "failed": 0}], "docs": ["<repo path>@<sha>"]}`, omitting what you do not have. The lead's acceptance closes the linked project task on it (D10): only these structured fields are read, each item is verified (the sha exists, the log matches the run ledger), and an item that does not verify is dropped, never trusted. Write every run artifact you cite as evidence (a test report, a gate output) to the session dir and cite that path; a tcount log stays in the run ledger's own `runs/` directory. Never cite an OS temp dir or the session scratchpad (`$TMPDIR`, `/var/folders/...`, `/private/tmp/...`): the OS purges those, and the evidence can be gone before a reviewer reads it.
   **Uncertainty dispositions.** Every `uncertainty` item opens with one disposition, after an optional `[HIGH]`/`[MEDIUM]`/`[LOW]` tag: `fixed: <what changed>`, `known-limit: <the limit>` (the lead writes it into the PR) or `not-applicable: <why it does not bear on the task>`. Example: `"[HIGH] known-limit: undo resets later edits; tracked as a follow-up"`. The `write_checks.py` hook stores the write and prints a note naming an item without one; the lead sees it at acceptance. Each line is its own item. A bug you found in your own deliverable is `fixed:`, or a BLOCKER, never a quiet known limit.
   **Write receipt (required, BEFORE any notify).** A gate can return this write (a `rev` that is not a positive integer; an undisposed amendment or uncertainty item only gets a note; a digit-string `rev` such as `"2"` and a `key_decisions` key are repaired instead, and the gate says so), and a returned write leaves the previous revision, or nothing, on disk. After the write, read `rev` back from the task file: `cat "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/tasks/{team_name}/{taskId}.json" | jq .metadata.handoff.rev`. If the write was returned, or the rev on disk is not the one you wrote, fix what the gate named and rewrite the full object; send nothing until the read-back shows your rev. The notify then states the rev read back from disk and the digest from the stored line. At completion a hook prints `HANDOFF on disk: rev N, digest <d>` to the lead: the same rev and digest as your notify means the accepted copy is the one you notified.
   **Run-evidence note (not a return).** An item that claims a run the ledger does not back (NO-RUN), counts no recorded run has (MISMATCH) or a clean count over a failed run (EXIT) does not return the write: the HANDOFF is stored, the `HANDOFF stored` line still prints, and you get one `Note on #<id>` line naming the items; the lead reads them at acceptance. Fix them in a later write by citing the tcount line or log path, or by writing `not run`; never re-run a live probe only to get a ledger entry. If the same write is returned for another reason (an uncertainty item), the note rides in that return.
   Only when the write fails for a reason you cannot fix (a tool error, not a gate's return), still send the payload-carrying notify and state the write failure in it; the lead treats the missing disk copy as an integrity finding, never as a reason to skip your submission. It is the one case the notify carries the HANDOFF.

2. **Notify the team-lead** (task id, rev and digest, never the HANDOFF):
   ```
   SendMessage(to="team-lead",
     message="[{sender}→team-lead] Task complete on #<id>, rev <n>, digest <d>.",
     summary="Task complete: [brief]")
   ```

   > `<d>` is the 12-hex digest your write's stored line printed (`HANDOFF stored on #<id> (owner <you>): rev <n>, digest <d>`). The lead reads the stored copy with `repact-payload <id> --digest <d>`; a mismatch tells them it changed after your notify. Repeating the HANDOFF in the message doubles what both of you read. The `summary` never carries payload content (it truncates at 200 chars).
   > After writing, read the task JSON back and confirm every field is present, non-empty, and ends on its intended final content — a sender-side output cut lands mid-JSON and surfaces as a write error, not as silence. This check holds at any size.

3. **SET `intentional_wait` and idle**:
   ```
   TaskUpdate(taskId, metadata={"intentional_wait": {
       "reason": "awaiting_lead_completion",
       "expected_resolver": "lead",
       "since": "<canonical_since() output: tz-aware ISO-8601 UTC>",
       "covers_since": "<the same value as since>"
   }})
   ```
   `since` is a clock reading, never a guess: run `date -u +%Y-%m-%dT%H:%M:%S+00:00` (the `canonical_since()` format) right before this write.

4. **Idle.** End the turn with ONE short line and never restate the HANDOFF in prose: your turn-end text reaches the lead as the idle notification, so a restatement delivers it a second time. The team-lead judges acceptance on the stored copy, read once after your notify arrives, and either:
   - **Accepts**: `TaskUpdate(taskId, status="completed")`, then the one-line wake. On wake, CLEAR `intentional_wait` and check `TaskList` for follow-up work; with none, reply with one line at most.
   - **Rejects**: writes `metadata.handoff_rejection = {reason, corrections, since, revision_number, rev}`, then sends the one-line wake. Follow §On Rejection below.

> ⚠️ Do NOT call `TaskUpdate(taskId, status="completed")` on your own task. The team-lead-as-completion-gate is the discipline; teammate self-completion bypasses HANDOFF inspection. Two narrow exemptions (signal-tasks; secretary session briefing + memory-save) are documented at the relevant agent bodies — those carve-outs apply only to those agents, not to you unless your agent body says so.

> **Why idle, not poll?** You cannot self-wake while idle. The team-lead's wake-signal `SendMessage` brings you back to read the acceptance/rejection. Trust the wake; do not poll `TaskList` speculatively.

After wake on acceptance, check `TaskList` for unblocked tasks you OWN or can claim — **including your PRE-ASSIGNED Task B** (owner already you, still `pending`). Claiming is a status flip, not only an ownership grab: pre-assigned → `TaskUpdate(taskId, status="in_progress")`; unowned → `TaskUpdate(taskId, owner="your-name", status="in_progress")`. Do this BEFORE any implementation work. If none, idle (you may be consulted or shut down).

## On Rejection (Wake-Signal Receipt)

If the team-lead rejects your teachback or HANDOFF, the rejection is on your task file (its `*_rejection` key) and disk is the only copy. The wake, `[team-lead→<you>] Task #<id>: rejected on disk, digest <d>.`, names the task and the digest and never carries the corrections. You learn of it one of three ways: `repact-wait` returns on the write and prints `next: repact-payload <id> --key rejection`; mid-turn, a `REJECTION <key> on #<id>` line arrives after a tool call; after a turn end, the wake brings you back. Your task remains `in_progress`. This is the seam-agnostic rule in [§On Wake: Disk-First Re-Read](teammate-waiting.md#on-wake-disk-first-re-read-seam-agnostic); the residual-race mitigation there (never act on a single empty read) applies to the read below.

**On wake**:

1. **CLEAR your existing `intentional_wait`**:
   ```
   TaskUpdate(taskId, metadata={"intentional_wait": None})
   ```

2. **Read the rejection from the task file**: `repact-payload <id> --key rejection`. It prints each open rejection scrubbed, under a header with its key, `revision_number`, `rev` and digest; the digest is the one the wake names. Exit 4 means none is open. `TaskGet` does NOT surface metadata — see [completion.md §`TaskGet` metadata-blindness reminder](../../../protocols/completion.md#completion-authority).

   The shape is `{"reason": str, "corrections": [str, ...], "since": ISO8601, "revision_number": int, "rev": int}`; `rev` is the HANDOFF rev the lead judged, always present when your HANDOFF has one.

3. **Revise**. For teachback rejection: rewrite `metadata.teachback_submit` per the corrections. For HANDOFF rejection: revise the deliverable (re-edit files, re-run tests, etc.) and rewrite `metadata.handoff`.

   > ⚠️ To rewrite, RE-SEND THE FULL OBJECT in ONE `TaskUpdate` call, and include each field the corrections did not touch, unchanged. A write that carries part of a nested sub-object REPLACES that sub-object, and it erases each field you omit. Then read the task file back and enumerate the keys of the object you wrote. Mechanism: [§Action: store teachback now](teammate-teachback.md#action-store-teachback-now).

4. **Re-submit on the SAME task** (do NOT create a new task):
   - Increment `metadata.revision_number`. The team-lead writes `revision_number=1` in the rejection record. On your first revision, increment to `2`. On each subsequent revision, increment again. This count is the rejection-cycle audit trail — it feeds the imPACT META-BLOCK 3-cycle signal, not harvest routing. It does NOT gate whether your revised content is preserved: the team-lead's acceptance (the single completion) emits whatever `metadata.handoff` holds at that moment, so the revised content reaches the journal regardless of the count.
   - `SendMessage` the team-lead in the same form as the first submission, with the new digest: `"[{sender}→team-lead] Revised teachback/HANDOFF on Task #{id} (revision {N}), digest <d>."` — teachback Step 2 ([teammate-teachback.md](teammate-teachback.md#action-store-teachback-now)) for a teachback, On Completion Step 2 for a HANDOFF. A revised HANDOFF increments `rev`, and its notify names the rev and digest from the stored line (the write receipt of On Completion Step 1). The team-lead reads the revision once from the task file.
   - SET a new wait: a fresh `since` and a `covers_since` equal to it (shape in SKILL.md §Waiting and waking).
   - Idle.

> **Revision visibility**: your revised content reaches institutional memory because of *when* the journal event is emitted, not because of `revision_number`. A rejection keeps your task `in_progress` and emits nothing. The team-lead's acceptance (their completion of your task) emits an `agent_handoff` journal event carrying whatever `metadata.handoff` holds at that moment, so a revision that lands BEFORE acceptance reaches the journal and harvest reads it there (drain-proof).
>
> **`agent_handoff` IS A MULTI-EVENT FAMILY. It is NOT one event for each task.** The emit marker is keyed on the handoff CONTENT together with the task and the occupant, so each DISTINCT handoff content for one task emits its own event. Do not read the journal copy as the accepted copy, and do not read the first match as the current one.
>
> **REWRITE `metadata.handoff` BEFORE the lead accepts.** A revision you write AFTER your task is completed emits again only when some later write fires an emit path on that task, and your own metadata write does not fire one. As a result, such a revision can reach the task file alone, and the task-store drain then removes it.

### HANDOFF Format

Spawned outside a team (a plain subagent with no task list), end your last response with this structured HANDOFF: the validate_handoff stop hook reads that prose. As a teammate your HANDOFF is the task metadata (see On Completion Step 1 above), which enables chain-read by downstream agents, and your turn ends in one line.

These are PROSE labels. When you store this same HANDOFF in task metadata, use the canonical `metadata.handoff` keys, in this order: `rev`, `amendments`, `produced`, `decisions`, `reasoning_chain`, `uncertainty`, `integration`, `open_questions`. Never convert a display label into a key — `Key decisions` is the label, `decisions` is the key.

```
HANDOFF:
1. Produced: Files created/modified
2. Key decisions: Decisions with rationale, assumptions that could be wrong
3. Reasoning chain (optional): How key decisions connect — "X because Y, which required Z." Helps downstream agents reconstruct your understanding, not just your conclusions.
4. Areas of uncertainty (PRIORITIZED):
   - [HIGH] fixed|known-limit|not-applicable: {description} — Why risky, suggested test focus
   - [MEDIUM] {disposition}: {description}
   - [LOW] {disposition}: {description}
5. Integration points: Other components touched
6. Open questions: Unresolved items
```

Items 1-2 and 4-6 are required. Item 3 (reasoning chain) is recommended — include it unless the task is trivial. Not all priority levels need to be present in Areas of uncertainty. If you have no uncertainties, explicitly state "No areas of uncertainty flagged."

## Completion Integrity

Only report work as ready for team-lead review if you actually performed the changes. Never fabricate a completion HANDOFF: the team-lead accepts on the stored copy your notify's digest names, before transitioning status to `completed`. If files don't exist, can't be edited, or tools fail, report a BLOCKER via `SendMessage`; never invent results.

## Amendments and rev

A directive the team-lead adds after dispatch is an `amend_<n>` top-level key on your task (`{"text", "at", "from", "kind"}`, plus `sha` when the kind is `commit`); it can also arrive mid-turn as `LEAD AMENDMENT amend_<n>` context (the D2 relay). Read the undisposed ones with `repact-payload <id> --key amend`. Before the HANDOFF, re-read the task file and give each `amend_<n>` key one disposition in `metadata.handoff.amendments`. Every key gets its entry there even when `decisions` or `produced` already describes the change: the directive-reflection check reads only `amendments`, so a key without an entry counts as undisposed. The dispositions:

- `applied: <where>`: done, and where it landed;
- `declined: <why>`: not done, with the reason;
- `disputed: <why>`: you think the ruling is wrong; say why and apply nothing until the lead answers;
- `ack`: informational, nothing to change.

A directive that reached you only in a message and has no `amend_<n>` key: re-read the task file first, the key may have landed since. If there is none, ask the lead to put it on the task and act on it meanwhile; record it and what you did with it in `decisions` ("lead message <time>: <directive>: applied in <where>").

After acceptance the task is closed. A `metadata.handoff` write on a `completed` task is returned ("accepted at rev N: message the lead instead"): the lead acted on the accepted copy, so send what changed by `SendMessage` and leave the deliverable as accepted until the lead answers.

Commit and push state is as of your write: say "as of this write: committed <sha>, not pushed", so a later push by the lead does not read as a contradiction.

`rev` is 1 on the first HANDOFF write and increases by 1 on each revision (a rejection re-submit, or a post-amendment update). It is a bare integer: the `handoff_ordering_gate.py` hook RETURNS a HANDOFF write whose `rev` is anything else (`"2"`, `"final-1"`, `2.5`, `true`, `0`, `null`). An empty `amendments` map (`{}`) states that the task carries no `amend_` keys.
