---
description: Use when stuck, to decide whether to get help or redo work
argument-hint: [e.g., similar errors keep occurring despite attempts to fix]
---
You hit a blocker: $ARGUMENTS

imPACT is S3 operational triage, not S5 escalation: a viability threat (security, data, ethics) is an algedonic HALT/ALERT instead ([algedonic.md](../protocols/algedonic.md)). Detail (algedonic contrast, output examples, failure taxonomy, re-entry lifecycle): [reference/imPACT/triage-detail.md](../reference/imPACT/triage-detail.md), load it when shaping a re-dispatch or redoing a phase.

## Task Operations

1. Read the blocker task (agents report "BLOCKER: …" via `SendMessage`; you create the blocker task and `addBlockedBy` the agent's task).
2. Triage (below).
3. Task-log boundary write: you witnessed the work stop, so set its task (Task B's `metadata.project_task`) to blocked WITHOUT asking, via the command; report what you wrote:
     python3 "{plugin_root}/bin/repact-task" set <ref> --status blocked
4. Delegate the resolution (`TaskCreate`), or self-resolve.
5. On resolution: `TaskUpdate(blocker_id, status="completed")`.
6. Task-log boundary write: set the same task back with `--status active`; report it.

**Diagnose, don't fix.** Knowing the fix ≠ permission to implement it: name what broke, who fixes it, and what they need.

**`impact_cycle_count`**: on each entry read it from the feature task file (`TaskGet` does not surface metadata), increment via `TaskUpdate`; at ≥ 3 emit ALERT (META-BLOCK). Reset to 0 on resolution.

## Triage

1. **Redo prior phase?** Is the issue upstream in P→A→C→T?
2. **Additional agents needed?**
3. **Is the agent recoverable?**

| Outcome | Action |
|---|---|
| **Redo prior phase** | Re-delegate the prior phase (see re-entry below) |
| **Augment present phase** | Re-invoke the agent with context, plus parallel agents |
| **Nested cycle** | A sub-task needs its own P→A→C→T: [nested-cycle.md](../reference/rePACT/nested-cycle.md) |
| **Terminate agent** | Last resort: `TaskStop("{agent-name}")` (force-stop; no `shutdown_request` first) + `TaskUpdate(taskId, status="completed", metadata={"terminated": true, "reason": "..."})`, then a fresh agent from the partial HANDOFF. This is the completion carve-out ([completion.md](../protocols/completion.md#carve-outs)) |
| **Not truly blocked** | Continue with clarified guidance |
| **Escalate to user** | 3+ cycles unresolved |

Report one line: `imPACT: [outcome] — [reason]`.

## Phase Re-Entry Task Protocol

Never reopen the completed phase task; mark it `completed` with `metadata={"redo_reason": "..."}`. `TaskCreate("<PHASE> (retry): {feature-slug}")`, set it `in_progress`, `addBlockedBy` it on the current phase, dispatch per [Teachback-Gated Dispatch](orchestrate.md#teachback-gated-dispatch), complete it, then retry the current phase with a new agent task. Same worktree.
