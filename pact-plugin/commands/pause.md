---
description: Pause the session, save its memory and state, and stop the helper agents
---
# PACT Pause Protocol

Pause for later resumption: the **memory-critical subset of wrap-up**. It consolidates and persists state; no worktree cleanup, no task deletion. Full cleanup is [wrap-up.md](wrap-up.md). Pause runs when the user picks "Pause work for now" in `/repacted:peer-review`, `/repacted:comPACT` or `/repacted:wrap-up`.

## When to Use

- PR open but not ready to merge, or no PR yet; resume later; end of day.

## Steps

> **CRITICAL**: Steps 1-3 MUST complete BEFORE step 6. The secretary must be alive to process HANDOFFs. Ensure the secretary first: see the on-demand secretary step in [bootstrap.md](bootstrap.md#step-2--on-demand-secretary) (TaskCreate → TaskUpdate(owner="secretary") → spawn if absent → SendMessage).

### 1. Memory Consolidation (Pass 2)

```
TaskCreate(subject="secretary: session consolidation (Pass 2)",
  description="Run Consolidation Harvest for team {team_name} as your pact-handoff-harvest skill describes; report a summary when done.")
TaskUpdate(taskId, owner="secretary")
```

### 2. Documentation Sync

Run `/repacted:pin-memory` (no arguments).

### 3. Orchestration Retrospective

```
TaskCreate(subject="secretary: save orchestration retrospective",
  description="Save orchestration calibration: context='Orchestration retrospective for {feature}', goal='Calibrate orchestration judgment via second-order observation', decisions=[variety accuracy, specialist fit, phase efficiency], entities=['orchestration_calibration', '{domain}']. Report summary when done.")
TaskUpdate(taskId, owner="secretary")
```

**Skip when** the session was trivial (single comPACT, no variety assessment).

### 4. Task Status Report

`TaskList`, then report "Session has N tasks (X completed, Y in_progress, Z pending)". Do NOT delete or complete any task: they must survive for resume. Keep the feature task `in_progress` (reopen it if wrap-up completed it): a list whose tasks are all completed is deleted.

Then show the session numbers and the unattended-run list exactly as [wrap-up §7](wrap-up.md#7-task-audit) says: run `python3 "{plugin_root}/bin/repact-session-numbers" --session-dir '{session_dir}'` and `python3 "{plugin_root}/bin/repact-unattended" list --session-dir '{session_dir}'`, and show their lines as printed, never filling in an `n/a`.

### 5. Write Paused State to Session Journal

Write `session_consolidated` (only when step 3 ran) then `session_paused` (PR number, branch, worktree, consolidation status; `session_init.py` reads it on resume). **Load [reference/pause/paused-state.md](../reference/pause/paused-state.md) and run its shell-clamped template as written**; `{true_or_false}` must be the literal `true` or `false`. A pause with no PR is valid: `pr_number` and `pr_url` are `null`; say where to resume in the optional `resume_hint`.

### 6. Shut Down Teammates

Stop each active teammate **by name**, 1 per turn; no `shutdown_request` first:
```
For each active teammate:
  TaskStop("{teammate_name}")
```
Treat a not-found / already-exited error as already-stopped success and CONTINUE: never abort mid-loop. Stop the secretary only after steps 1 and 3 finished. EXPECTED post-state: a lead-only roster (config and team identity survive); a send to a stopped teammate fails hard. Do NOT delete the team.

### 7. Report

"Session paused. PR #{N} open at {url}. Resume with `/repacted:peer-review`." No PR: "Session paused, no PR; work is on {branch}." If the Telegram bridge is active: `telegram_notify("Session paused. PR #{N} open at {url}.")`.
