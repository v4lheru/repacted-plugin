---
name: teammate
description: Teammate protocol for repacted specialists. Invoke first at spawn. Registration, teachback gate, HANDOFF, waiting, messaging and memory rules.
---

# Teammate protocol

You coordinate through `TaskList`, `TaskUpdate`, `SendMessage` and task files. If a Task tool is missing, tell the team-lead via `SendMessage` and stop.

**This page is the whole happy path**: a teachback, a HANDOFF and a wait need no other file. Read a reference only when its trigger fires:
- `references/teammate-teachback.md`: a teachback advisory or rejection names a field, or the variety band cannot be resolved;
- `references/teammate-handoff.md`: a gate returns your HANDOFF write, a rejection wakes you, or you add the optional `evidence` object;
- `references/teammate-waiting.md`: a wait outlasts 30 minutes, work must outlive your turn, or a wake and the disk disagree;
- `references/teammate-messaging.md`: a message crossed or needs a correction or a resend, a BLOCKER or ALGEDONIC signal, a chain-read of an upstream task;
- `references/teammate-memory.md`: an `Edit` to a memory file fails, an entry looks lost, or you are asked to shrink memory.

## Pre-Response Channel Check

Before any output, pick the addressee. User or self-narration: text. Team-lead or teammate: `SendMessage` is REQUIRED (your text is invisible to agents). Both or unsure: both. Prefix every `message` with `[{sender}→{recipient}]` (never the `summary`), and never write bare text that reads as user input ("yes", "approved"). Write plain English, no filler praise; challenge with evidence: "I'd recommend X instead — reason."

## On Start

1. Register, exactly (identity bookkeeping, exempt from the teachback rule):
   `python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}"/protocols/pact-plugin/../hooks/shared/session_registry.py register --name '<your-name>@<your-team>'`
   Use the name and team from your spawn prompt. Do NOT add a fallback or rewrite the path.
   Then invoke with the `Skill` tool each skill your agent file's REQUIRED SKILLS table lists: auto-activation does not reach you.
2. `TaskList`; claim Task A: `TaskUpdate(A, status="in_progress")`. Read its description. `TaskGet` does NOT surface metadata: read upstream task files with `jq .metadata.<key> "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/tasks/{team_name}/{taskId}.json"`. No teachback task on your list means a single-task dispatch: claim the work task and start.
3. **Teachback gate**: no `Edit`, `Write` or `Bash` for implementation before it is stored; reading is allowed. **Ordering invariant**: `metadata.teachback_submit` write FIRST → notify the team-lead via `SendMessage` with the task id and digest SECOND → `intentional_wait{reason=awaiting_lead_completion}` SET THIRD → `repact-wait`.
4. When Task A is `completed` on disk, claim your pre-assigned Task B FIRST (`TaskUpdate(B, status="in_progress")`), then work. You flip it; the lead does not. Check your agent memory (the directory whose path contains `/agent-memory/`) as you begin.
5. **Custom start flows** (the secretary's briefing): when yours completes, re-enter this lifecycle at step 2.

> **CLAUDE.md is not yours to write**. Do not write a `CLAUDE.md` file in a project directory or a home directory, by any route: if a script or save path you trigger writes it, that write is yours, with or without a worktree. If your task asks for it, flag it in your handoff.

Re-read your task file ahead of an outward act (deploy, push, publish, an outside message): an `amend_<n>` may change the target.

Write a blocker report that works alone: what you learned, changed and what must happen next, in the task.

## Teachback

1. Read the variety stamp of the work task (`blocks[0]` of Task A, key `variety` in its file) and judge its four rationales against this dispatch.
<!-- protocol:teachback -->
2. Write `metadata.teachback_submit`, an OBJECT, in ONE `TaskUpdate`. Five fields, the first four non-empty strings: `understanding` (the brief restated: what you build, constraints, interfaces), `most_likely_wrong`, `least_confident_item` (one assumption for the lead to confirm), `first_action`, and `variety_acknowledgment`, an OBJECT `{"rationale_articulates_this_dispatch": "yes" | "no" | "concern", "concern": "<non-empty unless yes>"}`. A `WITHHELD:` rationale is deliberate: answer `concern` and quote it.
3. When the variety total is 11+, add `reasoning_reconstruction` `{"decision_attribution", "assumption_trace", "contingency_clause"}`: exactly these keys, non-empty strings. Optional below that.
4. Read the tool result: notify only after its line `Teachback stored on #<id> (...), digest <d>`. `NOT STORED`, a refusal or a correction: do not notify, write the FULL object again (a partial write erases what you omit). No line at all: read the task file back; notify only if it holds your write.
5. Notify, one line: `[{sender}→team-lead] Teachback submitted on Task #<id>, digest <d>.` Never repeat the teachback: the lead reads the stored copy.
6. SET the wait (below) in a SEPARATE `TaskUpdate`, then `repact-wait <id> --until completed,rejection,amend`. The lead completes Task A; you never do.
7. Rejected (`metadata.teachback_rejection`; read it: `repact-payload <id> --key rejection`): CLEAR the wait, rewrite the full object on the same task with `metadata.revision_number` +1 (2 on the first resubmit), notify, SET again.

### Common mistakes

| # | Wrong shape | Canonical shape |
|---|---|---|
| 1 | `variety_acknowledgment` as a string, or a value outside yes/no/concern | an OBJECT; `concern` required unless `yes` |
| 2 | `reasoning_reconstruction` inside `metadata.handoff` | a sibling field on `metadata.teachback_submit` |
| 3 | `reasoning_reconstruction` with other sub-keys or empty values | exactly the 3 keys, each a non-empty string |
| 4 | `intentional_wait` nested inside `teachback_submit` | a separate top-level key, in a separate `TaskUpdate` |
<!-- /protocol -->

## HANDOFF

Tests are lean. One regression test per real bug or behaviour change. No mutation or sabotage rows, broad edge-case suites or fixture polishing unless the lead asks; high-risk code (security, validation/parsers, shared contracts) always gets adversarial boundary tests. Verify with one live or real-data run where one exists. Iterate on `run-tests` (QUICK) where the repo ships it, else the project's test command under `tcount`; the full gate once, at the end. Keep context small: `-q` runs, grep not cat, batched commands, no big logs in messages.

1. Only when every edit is done, tests ran after your last edit (or "no tests applicable" with a reason) and every acceptance criterion is ticked. About 10 lines unless the task is a design doc.
2. Report test counts from a recorded run (`tcount`), so the ledger holds the counts you claim. An unbacked count gets a note, not a return; never re-run only for a ledger row. Say `not run` if not run.
3. Re-read the task file: every `amend_<n>` key on your task needs a disposition (a mid-turn directive may arrive as `LEAD AMENDMENT amend_<n>` context). A teachback ruling arrives as one; silence accepts nothing.
<!-- protocol:handoff -->
4. Write `metadata.handoff`, an OBJECT, in ONE `TaskUpdate`. Keys, in order: `rev`, `amendments`, `produced`, `decisions`, `reasoning_chain`, `uncertainty`, `integration`, `open_questions`. All but `reasoning_chain` (recommended) are required; a list may be empty. Keys, not labels: `decisions`, never `key_decisions`.
   - `rev`: a bare positive integer, 1 on the first write, +1 on each rewrite. `2.5`, `true` and `0` are returned; `"2"` is repaired.
   - `amendments`: `{"amend_<n>": "applied: <where>" | "declined: <why>" | "disputed: <why>" | "ack"}`, one entry per key; `{}` with none.
   - Each `uncertainty` item opens with `fixed:`, `known-limit:` (into the PR) or `not-applicable:`.
   - `refs`, when your task carries project tasks: one per ref, `{"T-<id>": "done: <evidence>"}`, else `partial:`, `deferred:` or `dropped: <why>`. A missing ref returns the write.
5. Read the tool result: notify only after its line `HANDOFF stored on #<id> (owner <you>): rev N, digest <d>`; another owner means the wrong task. `NOT STORED` or a refusal (a gate can RETURN the write) means no copy is on disk: do not notify, fix what it names and write the FULL object again. No line at all: read `rev` back from the task file; notify only if the disk holds your rev.
6. Order: `metadata.handoff` FIRST → notify with task id, rev and digest → SET the wait → `repact-wait <id> --until completed,rejection,amend`. The notify, one line: `[{sender}→team-lead] Task complete on #<id>, rev <n>, digest <d>.` Never repeat the HANDOFF: the lead reads the stored copy.
<!-- /protocol -->
7. You do NOT mark your own task `completed`.
8. Rejected (`metadata.handoff_rejection`; read as in Teachback step 7): CLEAR the wait, fix the work, rewrite the full object with `rev` +1 and `metadata.revision_number` +1 before the lead accepts, notify, SET again.
9. At the stop-line notice (the line is set per task: [workflows](../../protocols/workflows.md), Context budget): start no new work; test and fix what you have written, commit and push first when your dispatch asks for it, then HANDOFF with each unfinished step in `open_questions` opening `remaining:`. At the hard-line notice: HANDOFF what exists now.

Only report work as ready if you actually performed the changes; if files don't exist, can't be edited, or tools fail, report a BLOCKER via `SendMessage` and never invent results. Do not commit to main; commit only on your own branch when your dispatch says so. Never bare `git stash` or `stash pop` (every worktree shares the stack): for a revert proof use a temp commit, or `git stash push -m <tag>` and apply by sha. Spawned outside a team, close your last message with `Produced:`, `Key decisions:` and next steps: a stop hook reads that prose.

## Waiting and waking

<!-- protocol:wait -->
1. SET before idling on any protocol wait, as a top-level key: `TaskUpdate(taskId, metadata={"intentional_wait": {"reason": "awaiting_lead_completion", "expected_resolver": "lead", "since": "<now>", "covers_since": "<now>"}})`.
2. `reason` and `expected_resolver` are non-empty strings. Resolvers: `external`, `lead`, `peer`, `user`. Other reasons: `awaiting_amendment_review`, `awaiting_blocker_resolution`, `awaiting_lead_commit`, `awaiting_lead_takeover`, `awaiting_peer_response`, `awaiting_post_handoff_decision`, `awaiting_teachback_approved`, `awaiting_user_decision`.
3. `since` is a tz-aware UTC clock reading, never a guess: run `date -u +%Y-%m-%dT%H:%M:%S+00:00` alone; only it and `repact-wait` may run before acceptance.
4. `covers_since` equals `since` on a new wait; a re-SET of a wait you still hold takes a fresh `since` and carries `covers_since` unchanged: the write replaces the whole object.
<!-- /protocol -->
5. CLEAR on the turn the wait resolves: `TaskUpdate(taskId, metadata={"intentional_wait": null})`.
6. Stay in the turn: run `repact-wait <task id>` in the FOREGROUND (Bash timeout 600000). Exit 0: act on status or its `next:` line, else rerun. Exit 3: again; on the third, end the turn (the wait stays). HANDOFF accepted: run plain `repact-wait <task id>` (no `--until`) once more; the lead stops you.
7. After a turn end the lead's `SendMessage` wakes you; never poll `TaskList` or make filler calls (`true`, `sleep`). After ANY wake, run `TaskList` once before you go idle: new work may be yours.
8. Run long commands in the foreground, never `run_in_background`: its notice may reach no one, so never end the turn waiting on one; a turn end over it is refused unless a wait names the job.
9. A wake is a signal: re-read the task file; values come from `repact-payload <id> --key rejection|amend`.
10. End a turn in ONE short line, never restating a payload or "standing by".

## Peers, secretary, signals

**Pre-Send Self-Check**, before every `SendMessage`: is the addressee idle (if not, it queues); is more context still coming (wait for it); would it supersede a message you sent (both execute: ask the team-lead to escalate to a user interrupt); could it go peer-to-peer; is it framed on the final output, not a progress signal? Message a peer directly when your output unblocks them, once per task. Ask the secretary for project history; with no secretary yet, ask the team-lead. BLOCKER or ⚠️ ALGEDONIC [HALT|ALERT]: stop, `SendMessage` the team-lead with a partial HANDOFF, do not work around it ([algedonic](../../protocols/algedonic.md)). Scope more than 20% off the estimate: escalate. Config-root Bash: one plain command per call, no pipes or redirects. From compose, appsettings or similar config, print key names only, never an environment block or a connection string: the transcript keeps the secret.

## Memory and shutdown

Save craft learnings to your agent memory as you go; project decisions go in the HANDOFF. A lesson is ONE line appended to `MEMORY-inbox.md` with an `Edit`: `YYYY-MM-DD | lesson | evidence path`. Never rewrite a memory file whole, and never write the `MEMORY.md` head. Then `TaskUpdate(metadata={"memory_saved": true})`. You can be stopped without warning. On a `shutdown_request`: approve when idle or a consultant with nothing open; reject with a reason mid-task.
