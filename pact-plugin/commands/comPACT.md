---
description: Send specialist agents on a small, self-contained task, with no full plan needed
argument-hint: [backend|frontend|database|prepare|test|architect|devops|security|qa] <task>
---
Dispatch concurrent specialists for this self-contained task: $ARGUMENTS

**MANDATORY: invoke concurrently for independent sub-tasks.** Sequential requires explicit file conflict or data dependency. If the task contains multiple independent items (bugs, endpoints, components), dispatch multiple specialists together — same type or mixed types — unless they share files.

> ⚠️ **Independent ≠ same domain.** "Fix CSS layout + add server logging" = 1 frontend-coder + 1 backend-coder in parallel. The key criterion is independence (no shared files, no data dependencies), not domain uniformity.

Selection keywords, concurrency examples, output style, signals and escalation: [selection-and-escalation.md](../reference/comPACT/selection-and-escalation.md) (load when the specialist is unclear or the task grows).

## Task Hierarchy

Feature task "{verb} {feature}" (`in_progress`, `addBlockedBy` = every agent task), stamped with the feature-level variety total, whose journaled `variety_assessed` event marks the arc start the wrap-up Orchestration Retrospective scopes its reads from: `TaskCreate(subject="{verb} {feature}", metadata={"variety": {"total": N}})` (4-16; advisory). The hook journals its `variety_assessed` event; do not write it. Then, per specialist (persona §11): a dispatch whose complexity total is 6 or less is single-task dispatch (the work task alone: no teachback task, no `addBlockedBy`); above 6, one Teachback-Gated Dispatch pair. Then [After Specialist Completes](#after-specialist-completes).

## Specialist Selection

Shorthands: `backend`, `frontend`, `database`, `prepare`, `test`, `architect`, `devops`, `security`, `qa`. Otherwise auto-select on clear keywords or file types, or `AskUserQuestion` "Which specialist should handle this task?" with the 2-3 likeliest.

## S2 Light Coordination (Required Before Concurrent Dispatch)

List each sub-task's files; sequence or split overlaps (first agent's convention wins); put boundaries in each prompt; include later drift from `file-edits.json`; persist `TaskUpdate(codePhaseTaskId, metadata={"s2_boundaries": {...}, "established_conventions": {...}})`. **If conflicts cannot be resolved**: sequence the work.

## Pre-Invocation (Required)

1. **Worktree** — reuse the feature's, else `/repacted:worktree-setup`.
2. **Session team** — The `{team_name}` team is provisioned automatically by the platform — use it for dispatches; you do not create it.
3. **S2 coordination** (if concurrent).
4. **Task-log boundary write** — WITNESSED start: mark the project task `active` WITHOUT asking, via the command, ONCE PER REF the unit owns (the link is what the HANDOFF gate and the acceptance read; a ref only named in the brief is a note, never closed; the HANDOFF owes `handoff.refs` one disposition per ref, so say so in the brief); report the write or refusal and carry on.
   ```bash
   python3 "{plugin_root}/bin/repact-task" start <ref> --ptask {team_name}/<task id>
   ```
5. **Repo notes** — read the repo's `.claude/pact-dispatch-notes.md` if present; put the lines that apply in each brief.

## Teachback-Gated Dispatch

Read reference/dispatch/teachback-gated-dispatch.md before your first dispatch this session. At a total of 6 or less, create only the Task B below (no Task A). Its carve-outs cover auditor signal-tasks and the secretary. Each dispatch site below gives only its mission fields and its own `agent_dispatch` journal write (comPACT has no phase task for the hook to derive it from).

## Invocation

### Multiple Specialists Concurrently (Default)

When the task contains multiple independent items, invoke multiple specialists together with boundary context. For each specialist needed, follow the steps for [Teachback-Gated Dispatch](#teachback-gated-dispatch) with `name="{specialist-name}"`, `subagent_type="pact-{specialist-type}"`:

- Task A subject: `"{specialist-name}: TEACHBACK for {sub-task}"`.
- Task B subject: `"{specialist-name}: {sub-task}"`.
- Task B's `description` carries the comPACT-concurrent mission: "comPACT mode (concurrent): You are one of [N] specialists working concurrently.\nYou are working in a git worktree at [worktree_path]. All file paths must be absolute and within this worktree.\nAs a teammate, do NOT write a `CLAUDE.md` file in a project directory or a home directory. This covers each route to that write, not only an `Edit` or a `Write` you issue: if a script you run, a command you invoke, or a save path you trigger writes the file, that write is yours. This applies with or without a worktree. The orchestrator manages those files. If your task mentions updating `CLAUDE.md`, flag it in your HANDOFF instead.\n\nYOUR SCOPE: [specific sub-task]\nOTHER AGENTS' SCOPE: [what others handle]\n\nWork directly from this task description.\nIf upstream task IDs are provided, read their task files for prior decisions; `TaskGet` does NOT surface metadata.\nCheck docs/plans/, docs/preparation/, docs/architecture/ briefly if they exist.\nDo not create new documentation artifacts in docs/.\nStay within your assigned scope.\n\nFIRST claim this task (`TaskUpdate` status=in_progress) before any implementation tool-use — it is pre-assigned to you but still pending; you flip it, not the lead.\n\nTesting: New unit tests for logic changes. Fix broken existing tests. Run test suite before HANDOFF.\nImport hygiene (pact-coding-standards): before the suite, run `bash {plugin_root}/skills/pact-coding-standards/scripts/lint-check.sh --files` on each .py file you modified; record its verdict line in HANDOFF `produced`.\n\nIf you hit a blocker, STOP and `SendMessage` it to the team-lead.\n\nTask: [this agent's specific sub-task]"
- **Journal event** before spawning (the hook journals `variety_assessed` from the stamp; this `agent_dispatch` is yours, comPACT having no phase task to derive it from):
   Then `agent_dispatch`:
   ```bash
   "{plugin_root}/bin/repact-journal-write" --site comPACT.md --type agent_dispatch --session-dir '{session_dir}' --stdin <<'JSON'
   {"agent": "{specialist-name}", "task_id": "{taskId}", "phase": "CODE", "scope": ["{assigned_paths}"]}
JSON
   ```

> ⚠️ **Heredoc-stdin contract**: every journal write here uses `--stdin <<'JSON' ... JSON` (quoted delimiter, closing `JSON` at column 0) so template values pass through unexpanded; still emit JSON-valid strings.


Spawn all specialists in parallel (multiple `Agent` calls in one response). For parallel or novel work, include "Send progress signals per teammate-messaging.md §Progress Signals" in each dispatch prompt. **After all complete**: verify no conflicts, run the full suite.

### Single Specialist `Agent` (When Required)

Only when the task is atomic, sub-tasks share files or depend on each other, or conventions are not yet set.

**Dispatch the specialist** — follow the steps for [Teachback-Gated Dispatch](#teachback-gated-dispatch) with `name="{specialist-name}"`, `subagent_type="pact-{specialist-type}"`:

- Task A subject: `"{specialist-name}: TEACHBACK for {task}"`.
- Task B subject: `"{specialist-name}: {task}"`.
- Task B's `description` carries the comPACT mission: "comPACT mode: Work directly from this task description.\nYou are working in a git worktree at [worktree_path]. All file paths must be absolute and within this worktree.\nAs a teammate, do NOT write a `CLAUDE.md` file in a project directory or a home directory. This covers each route to that write, not only an `Edit` or a `Write` you issue: if a script you run, a command you invoke, or a save path you trigger writes the file, that write is yours. This applies with or without a worktree. The orchestrator manages those files. If your task mentions updating `CLAUDE.md`, flag it in your HANDOFF instead.\nIf upstream task IDs are provided, read their task files for prior decisions; `TaskGet` does NOT surface metadata.\nCheck docs/plans/, docs/preparation/, docs/architecture/ briefly if they exist.\nDo not create new documentation artifacts in docs/.\nFocus on the task at hand.\n\nFIRST claim this task (`TaskUpdate` status=in_progress) before any implementation tool-use — it is pre-assigned to you but still pending; you flip it, not the lead.\n\nTesting: New unit tests for logic changes (optional for trivial changes). Fix broken existing tests. Run test suite before HANDOFF.\nImport hygiene (pact-coding-standards): before the suite, run `bash {plugin_root}/skills/pact-coding-standards/scripts/lint-check.sh --files` on each .py file you modified; record its verdict line in HANDOFF `produced`.\n\n> Smoke vs comprehensive tests: These are verification tests; HIGH-RISK code (security, validation/parsers, shared contracts) also gets its adversarial boundary tests now. The rest is TEST phase work.\n\nIf you hit a blocker, STOP and `SendMessage` it to the team-lead.\n\nTask: [user's task description]"
- **Journal event** before spawning, `agent_dispatch` (yours; the hook journals `variety_assessed`):
   Then `agent_dispatch`:
   ```bash
   "{plugin_root}/bin/repact-journal-write" --site comPACT.md --type agent_dispatch --session-dir '{session_dir}' --stdin <<'JSON'
   {"agent": "{specialist-name}", "task_id": "{taskId}", "phase": "CODE", "scope": []}
JSON
   ```

## Auditor Dispatch

An auditor is dispatched alongside coders unless explicitly skipped; to skip, output on its own line:

> **Auditor skipped**: [justification]

**Dispatch is mandatory when**: variety >= 7, 3+ parallel coders, security-sensitive code, or prior architecture drift, unless the default agent cap (2 at once) is full: then the ONE reviewer's post-artifact audit replaces it, and the skip line says so. Its task carries `metadata: {"completion_type": "signal"}`.

## After Specialist Completes

- [ ] **Receive HANDOFF**; **agreement verification** by `SendMessage` before committing.
- [ ] **Run tests**; on failure return to the specialist (new agent task).
- [ ] **Create atomic commit(s)** — lead owns commits; specialists stage, SET `intentional_wait` (`awaiting_lead_commit`, `lead`), send "stage-ready"; confirm by `amend_<n>` (kind `commit`) or completion.
- [ ] **Journal events**: After each commit, write a `commit` event:
  ```bash
  "{plugin_root}/bin/repact-journal-write" --site comPACT.md --type commit --session-dir '{session_dir}' --stdin <<'JSON'
  {"sha": "{short_sha}", "message": "{first_line}", "phase": "CODE"}
JSON
  ```
- [ ] **Verify agent task completion** via `TaskList`; if still `in_progress`, `TaskUpdate(taskId, status="completed")`.
- [ ] **Task-log boundary write** — verified complete is WITNESSED: the acceptance hook closes every linked ref the HANDOFF disposed as `done` and prints what it left open; close those by hand, with the commit as evidence, `--remaining "<title>"` for a part not done; report it (or the refusal).
  ```bash
  python3 "{plugin_root}/bin/repact-task" done <ref> --evidence commit:<sha>
  ```
- [ ] **Harvest HANDOFFs on demand, not by default** (persona §13, lead-reference §Harvest Triggers): only when the user asks or a HANDOFF carries a decision worth keeping across sessions; otherwise the consolidation harvest at wrap-up or pause covers it. When you do, ensure the secretary first: see the on-demand secretary step in [bootstrap.md](bootstrap.md#step-2--on-demand-secretary) (TaskCreate → TaskUpdate(owner="secretary") → spawn if absent → SendMessage).
  ```
  TaskCreate(subject="secretary: harvest pending HANDOFFs",
    description="Harvest HANDOFFs for team {team_name}. Follow the Standard Harvest workflow in your pact-handoff-harvest skill. Report summary when done.")
  TaskUpdate(taskId, owner="secretary")
  ```
- [ ] **Journal event**: Write `phase_transition` to mark comPACT completion:
  ```bash
  "{plugin_root}/bin/repact-journal-write" --site comPACT.md --type phase_transition --session-dir '{session_dir}' --stdin <<'JSON'
  {"phase": "CODE", "status": "completed", "skip_reason": "", "metadata": {"workflow": "comPACT"}}
JSON
  ```
- [ ] **`TaskUpdate`**: Feature task status = "completed"

> ⚠️ **Stop each specialist once its task is accepted and nothing of its awaits verification** (persona §11). The next step is the user's: ask first, then act.

**Next steps** — After commit, use `AskUserQuestion` to ask: "Work committed. What next?"

| User's decision | Specialists | Next action |
|----------------|-------------|-------------|
| **Yes, create PR** (Recommended) | **Stopped once its task is accepted** — review fixes go to the code's author if it is still running, else to a fresh coder | Invoke `/repacted:peer-review`; a reviewer with open findings stays until they are verified. The merge is the user's decision (via `AskUserQuestion`). |
| **Continue working** | **Stopped once its task is accepted** — a follow-up goes to a fresh agent (Reuse vs. Spawn table) | Do nothing — let the user continue. More work may follow via `/repacted:comPACT` or `/repacted:orchestrate`. |
| **Pause work for now** | **Shut down after consolidation** — pause preserves knowledge | Invoke `/repacted:pause` — consolidates memory, persists state, shuts down teammates. Worktree persists; resume later. |

**Blocker reported**: run `/repacted:imPACT`; escalate to `/repacted:orchestrate` if the task outgrows self-contained scope (re-assessed variety 7+).
