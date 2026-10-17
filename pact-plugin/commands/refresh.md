---
description: Save progress and stop helper agents mid-task so you can run /compact
disable-model-invocation: true
---
# PACT Refresh Protocol

Reset context mid-workstream without losing state; the workstream CONTINUES (nothing closed, merged or cleaned). PR-open pause: [pause.md](pause.md); session end: [wrap-up.md](wrap-up.md). The harvest and its drain confirmation are MANDATORY.

## Constraints (apply to every step)

- **LEAD-FRAME-ONLY**: spawn, stop and message teammates from the lead frame.
- **Never clean worktrees, branches, or commits.** Verification is read-only.
- **Never complete or delete tasks**: feature, phase and HALT/algedonic tasks must survive.
- **Never write under `{config_dir}/pact-refresh/`** (legacy; `{config_dir}` = `$CLAUDE_CONFIG_DIR` or `$HOME/.claude`); state lives ONLY in the journal.

## Steps

> **CRITICAL**: step 2 drain-confirmed BEFORE step 5. Ensure the secretary first: see the on-demand secretary step in [bootstrap.md](bootstrap.md#step-2--on-demand-secretary) (TaskCreate → TaskUpdate(owner="secretary") → spawn if absent → SendMessage).

### 1. Preflight — degenerate case

No in_progress feature task? Say refresh runs harvest-only (`/repacted:wrap-up` may fit better); on confirm, write the minimal step-4 event.

### 2. HARVEST — memory consolidation (Pass 2)

```
TaskCreate(subject="secretary: session consolidation (Pass 2)",
  description="Run Consolidation Harvest for team {team_name}. Follow the Consolidation Harvest workflow in your pact-handoff-harvest skill. Report summary when done.")
TaskUpdate(taskId, owner="secretary")
```

**Drain-confirmation gate (MUST)**: READ the task (`TaskGet` or its JSON) and confirm `completed` before any shutdown; else wait or nudge.

### 3. PERSIST/VERIFY — read-only state check + payload computation

Per worktree, `git status --porcelain`; report uncommitted changes, never clean or commit. Compute the checkpoint fields step 4 lists (HALT/algedonic tasks, feature task, `team_name`, `next_phase`, worktrees, open PR).
Task log: note each active task's state (`repact-task set <ref> --note`), then `repact-task commit` (with the task-log question of bootstrap step 5 declined or left open, skip both).

### 4. Write the checkpoint to the session journal

**Load [reference/refresh/checkpoint.md](../reference/refresh/checkpoint.md) and run its shell-clamped template as written.**

### 5. SHUTDOWN teammates

Stop each active teammate **by name**, 1 per turn; no `shutdown_request` first:
```
For each active teammate:
  TaskStop("{teammate_name}")
```
A not-found error is already-stopped success: CONTINUE. EXPECTED post-state: a lead-only roster. Do NOT delete the team.

### 6. STANDBY

Report: "Context checkpoint complete. Run /compact now, then /repacted:bootstrap to resume mid-flight." Until bootstrap runs:

- **FORBID new dispatches**.
- **RESPAWN-BEFORE-SEND**: messaging a pre-refresh name (the secretary too) resurrects its stale transcript.
- **Expect and IGNORE stragglers**: a late pre-stop message is not a live teammate; never reply.

Telegram bridge active: `telegram_notify` the same line.
