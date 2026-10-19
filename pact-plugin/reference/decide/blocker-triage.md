# Decide: blocker triage

> Loaded from [decide.md](../../commands/decide.md) when a teammate reports a blocker, or the same failure keeps coming back. The task-log writes for a blocker (`blocked`, then `active` again) are decide.md's lines.

Blocker triage is operational: how do we proceed? A threat to the work itself (security, data, ethics) is a STOP or ALERT signal instead ([stop-signals.md](../../protocols/stop-signals.md)), and so is a third triage of the same blocker without a fix.

## Task operations

1. Read the blocker task (agents report "BLOCKER: …" via `SendMessage`; you create the blocker task and `addBlockedBy` the agent's task).
2. Triage (below).
3. Task-log write: you witnessed the work stop, so set its task (the work task's `metadata.project_task`) to `blocked` WITHOUT asking, with decide.md's line; report what you wrote.
4. Delegate the resolution (`repact dispatch`), or resolve it yourself.
5. On resolution: `TaskUpdate(blocker_id, status="completed")`.
6. Task-log write: set the same task back to `active` with decide.md's line; report it.

**Diagnose, don't fix.** Knowing the fix is not permission to implement it: name what broke, who fixes it, and what they need.

**`impact_cycle_count`**: on each triage read it from the feature task file (`TaskGet` does not surface metadata) and increment it via `TaskUpdate`; at 3 or more, raise ALERT (META-BLOCK). Reset it to 0 on resolution.

## Triage

1. **Redo a prior phase?** Is the issue upstream in P→A→C→T?
2. **More agents needed?**
3. **Is the agent recoverable?**

| Outcome | Action |
|---|---|
| **Redo prior phase** | Re-dispatch the prior phase (re-entry below) |
| **Augment present phase** | Re-dispatch the agent with context, plus parallel agents |
| **Nested cycle** | A sub-task needs its own P→A→C→T: [nested-cycle.md](../rePACT/nested-cycle.md) |
| **Terminate agent** | Last resort: `TaskStop("{agent-name}")` (force-stop; no `shutdown_request` first) + `TaskUpdate(taskId, status="completed", metadata={"terminated": true, "reason": "..."})`, then a fresh agent from the partial HANDOFF. This is the completion carve-out ([completion.md](../../protocols/completion.md#carve-outs)) |
| **Not truly blocked** | Continue with clarified guidance |
| **Escalate to user** | 3+ cycles unresolved |

Report one line: `blocker: [outcome] — [reason]`. The user sees the outcome, not the diagnosis; they can always ask why.

## Gather context

Before triaging, check quickly: the plan in `docs/plans/`, the prior phase outputs (`docs/preparation/`, `docs/architecture/`), and, if progress monitoring was on, whether the agent was converging, exploring or stuck before the blocker. This tells an isolated blocker from a systemic one.

### Why the conversation broke down

After the triage picks an outcome, name the breakdown type: it shapes the next brief ("the prior agent misunderstood the interface; the contract is X" beats "redo this").

| Breakdown | Symptoms | Likely outcome | Recovery |
|---|---|---|---|
| **Misunderstanding** | Wrong output, no errors: the agent built the wrong thing | Augment present phase | Correct the brief: say what was misunderstood, re-dispatch |
| **Derailment** | Loops on the same error or approach | Terminate agent | Fresh agent with a different framing, not a plain retry |
| **Discontinuity** | Lost context, stale assumptions | Augment present phase | Rebuild from memory and task files, re-dispatch with it |
| **Absence** | Started without enough upstream work | Redo prior phase | Do the missing phase first |

## Phase re-entry

Re-entry stays in the current worktree; no new worktree is created. Complete the blocker task first (step 5).

1. Never reopen the completed phase task: it is the record. Mark it `completed` with `metadata={"redo_reason": "..."}`.
2. `TaskCreate("<PHASE> (retry): {feature-slug}")` and set it `in_progress`.
3. Block the current phase on it: `TaskUpdate(currentPhaseId, addBlockedBy=[retryPhaseId])`.
4. Dispatch the retry phase with `repact dispatch`.
5. On its completion, `TaskUpdate(retryPhaseId, status="completed")` unblocks the current phase.
6. Retry the current phase with a new agent task built on the updated outputs.

A failed retry counts toward the 3-cycle ALERT.
