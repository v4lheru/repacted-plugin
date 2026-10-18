---
description: Build a feature with a team of AI agents
argument-hint: [e.g., implement feature X]
---
Orchestrate specialist PACT agents through the PACT workflow to address: $ARGUMENTS

On demand: [phase-skip-gates.md](../reference/orchestrate/phase-skip-gates.md) (before skipping any phase), [scoped-orchestration.md](../reference/orchestrate/scoped-orchestration.md) (scope detection after PREPARE, nested cycles), [assessment-and-signals.md](../reference/orchestrate/assessment-and-signals.md) (load when scoring variety, before a CODE dispatch (QDCL), or on a signal or blocker).

---

## Task Hierarchy

Create upfront: the feature task "{verb} {feature}" (`in_progress`) and the phase tasks "PREPARE/ARCHITECT/CODE/TEST: {feature-slug}", chained ARCHITECT blockedBy PREPARE, CODE blockedBy ARCHITECT, TEST blockedBy CODE. When decomposition fires after PREPARE, ARCHITECT and CODE are skipped (`decomposition_active`) and ATOMIZE / CONSOLIDATE phases are created (see the scoped reference).

Per phase: set it `in_progress`; create the agent task pairs (below); `addBlockedBy` them on the next phase; spawn; monitor by `SendMessage` and `TaskList`; complete the phase. Phase start, completion and skip events are journaled by the hook.

### Teachback-Gated Dispatch

Read reference/dispatch/teachback-gated-dispatch.md before your first dispatch this session. Read the repo's `.claude/pact-dispatch-notes.md` too, if present: its relevant lines go into each brief. It also covers signal-tasks, the secretary and imPACT force-termination. Each phase site below gives only its subjects and its Task B `description` fields (CONTEXT / MISSION / INSTRUCTIONS / GUIDELINES per §13).

At every phase site: the hook journals the dispatch's `variety_assessed` and `agent_dispatch` events from the wiring writes, do not write them; spawn with the canonical dispatch form; stop each completed-phase teammate once its task is accepted (persona §11). **Before the next phase**, every phase checks: the specialist HANDOFF received; a blocker reported → `/repacted:imPACT`; the **S4 Checkpoint** ([workflows.md §S4 Checkpoints](../protocols/workflows.md#s4-checkpoints)): environment stable? model aligned? plan viable? (variety 7+: optionally query the secretary for an S4 pattern check, see [pact-orchestrator §Memory Management](../agents/pact-orchestrator.md#memory-management)).

#### Artifact gate (PREPARE and ARCHITECT)

For a phase that **ran** (not skipped), its per-feature artifact MUST exist on disk and be non-empty before you complete the phase task or emit below: PREPARE at `docs/preparation/{feature}.md` (plus any additional produced files, e.g. `environment-model-{feature}.md`); ARCHITECT at `docs/architecture/{feature}.md` (fix-up work on someone else's repo: `{session_dir}/architecture/{feature}.md`) and any other produced files. A missing or empty artifact is a **failure to investigate**: do **NOT** complete the phase or emit; find out why (wrong location, nothing produced, a different slug) and resolve it. Then **emit `artifact_paths`**: glob the worktree's `docs/preparation/` or `docs/architecture/` directory (or `{session_dir}/architecture/`) and write a path-only `artifact_paths` journal event so the artifact is recoverable after garbage-collection independently of any HANDOFF. Enumerate the full absolute path list yourself (do NOT read paths from a HANDOFF: a recovery pointer must not depend on the thing it recovers). If the glob finds nothing, do NOT emit an empty event (the missing-artifact case above already covers that anomaly). Then **complete the phase task**, ONLY after the check and the emit: `TaskUpdate(phaseTaskId, status="completed")`. This ordering is load-bearing: the emit MUST land before the phase-task completion so the durability pointer is on disk by the time completion is observed. A **skipped** phase is exempt: it legitimately produces no artifact, completes earlier with `metadata.skipped=true` and runs neither the check nor the emit.
```bash
# Enumerate this feature's artifacts (worktree-isolated → this feature only); workflow is "prepare" or "architect".
# Emit only when ≥1 non-empty path is found; an empty result is the missing-artifact anomaly, not an event.
"{plugin_root}/bin/repact-journal-write" --site orchestrate.md --type artifact_paths --session-dir '{session_dir}' --stdin <<'JSON'
{"workflow": "{prepare|architect}", "feature": "{feature}", "paths": ["{absolute_path_1}", "{absolute_path_2}"], "task_id": "{phase_task_id}"}
JSON
```

**Skipped phases**: Mark directly `completed` (no `in_progress` — no work occurs):
`TaskUpdate(phaseTaskId, status="completed", metadata={"skipped": true, "skip_reason": "{reason}"})`
Valid reasons: `"plan_section_complete"`, `"structured_gate_passed"`, `"decomposition_active"`.
<!-- Skip reason semantics:
  - "plan_section_complete": Plan exists AND section passed completeness check (Layer 2)
  - "structured_gate_passed": All structured analysis questions answered concretely (Layer 3)
  - "decomposition_active": Scope detection triggered decomposition; sub-scopes handle this phase via a nested cycle
-->

---

## Before Starting

Assess variety per [workflows.md §Variety](../protocols/workflows.md#variety) (novelty, scope, uncertainty, risk, 1-4 each): Low 4-6 → offer comPACT; Medium 7-10 → standard orchestrate; High 11-14 → recommend plan-mode first; Extreme 15-16 → a research spike. One-line output, e.g. `Variety: Medium (8) — standard orchestrate with all phases`.

**Persist `variety`**: `TaskUpdate(featureTaskId, metadata={"variety": {"novelty": N, "scope": N, "uncertainty": N, "risk": N, "total": N}})`. The hook journals the feature-level `variety_assessed` event; do not write it. After compaction, recover it with `python3 "{plugin_root}/hooks/shared/session_journal.py" read-last --session-dir '{session_dir}' --type variety_assessed --exclude-scope dispatch` (without the exclusion a per-dispatch mirror comes back as the feature assessment); fall back to the feature task file's `metadata.variety` (`TaskGet` does NOT surface metadata). No variety data means hard gates cannot be evaluated, so phases run.

**Cost preview**: before each phase's first dispatch, print the line of `python3 "{plugin_root}/bin/repact-cost" --preview <N>` (N specialists) to the user as it is.

> ⚠️ **Heredoc-stdin contract for journal events**: every journal write uses `--stdin <<'JSON' ... JSON` (quoted delimiter) so substituted values pass through unexpanded; substitute only JSON-valid strings (escape `\"`, `\\`, control characters).

> **Task-log boundary write** — starting the feature is a transition you WITNESSED: mark its project task `active`, linked to the feature task, WITHOUT asking, via the command, once per ref the feature owns (HANDOFFs owe `handoff.refs` per ref); report the write or refusal and carry on.
>
> ```bash
> python3 "{plugin_root}/bin/repact-task" start <ref> --ptask {team_name}/<feature task id>
> ```

When variety is Low (4-6), offer the choice:

```
AskUserQuestion(
  question: "This task appears routine. Which workflow?",
  options: ["comPACT (Recommended)", "Full orchestrate"]
)
```

## Execution Philosophy

**MANDATORY: Invoke concurrently unless blocked.** The burden of proof is on sequential dispatch: without a specific file conflict or data dependency, invoke concurrently, in every phase.

1. **Set up worktree**: reuse the feature's worktree, else `/repacted:worktree-setup`.
2. **Session team**: The `{team_name}` team is provisioned automatically by the platform — use it for dispatches; you do not create it.
3. **Check for plan** in `docs/plans/`: `/repacted:orchestrate` on a PENDING APPROVAL or APPROVED plan sets it IN_PROGRESS; ask on BLOCKED, IN_PROGRESS or SUPERSEDED.

### Phase Transitions

When a phase's tasks complete, create the next phase's tasks and spawn its teammates; stop the earlier teammates once their tasks are accepted (persona §11). **Phase completed**: after the hook's phase event, write the `checkpoint`:
```bash
"{plugin_root}/bin/repact-journal-write" --site orchestrate.md --type checkpoint --session-dir '{session_dir}' --stdin <<'JSON'
{"phase": "{PHASE}", "completed_phases": [...], "active_agents": [{"name": "...", "task_id": "...", "status": "..."}], "variety": VARIETY_DICT_OR_NULL, "pending_phases": [...], "safe_to_retry": true}
JSON
```

## Context Assessment: Phase Skip Decision Flow

Phases run by default; a skip must be earned. PREPARE or ARCHITECT is skipped only when all three layers pass (variety hard gates, plan completeness, structured analysis): **load [phase-skip-gates.md](../reference/orchestrate/phase-skip-gates.md) before any skip**. CODE always runs; TEST skips only on all four of its conditions. When ambiguous, the phase runs.

### PREPARE Phase → `pact-preparer`

Plan sections to pass: "Preparation Phase", "Open Questions > Require Further Research".

**Dispatch `pact-preparer`** — follow the steps for [Teachback-Gated Dispatch](#teachback-gated-dispatch) with `name="preparer"`, `subagent_type="pact-preparer"`:

- Task A `"preparer: TEACHBACK for {feature}"`; Task B `"preparer: research {feature}"` (the subjects). Task B fields:
    - CONTEXT: [task description, plan sections (if any)]
    - MISSION: [research mission]
    - INSTRUCTIONS: [preparer-specific instructions]
    - GUIDELINES: "Reference the approved plan at `docs/plans/{slug}-plan.md` for full context."

**Before next phase**: the [artifact gate](#artifact-gate-prepare-and-architect) (`workflow: prepare`, `TaskUpdate(prepareTaskId, ...)`), then the shared checks above.

Concurrent dispatch: several independent research areas → several preparers. After PREPARE, run scope detection and the PREPARE→ARCHITECT coupling ([scoped-orchestration.md](../reference/orchestrate/scoped-orchestration.md)).

### ARCHITECT Phase → `pact-architect`

Plan sections to pass: "Architecture Phase", "Key Decisions", "Interface Contracts".

**Dispatch `pact-architect`** — follow the steps for [Teachback-Gated Dispatch](#teachback-gated-dispatch) with `name="architect"`, `subagent_type="pact-architect"`:

- Task A `"architect: TEACHBACK for {feature}"`; Task B `"architect: design {feature}"` (the subjects). Task B fields:
    - CONTEXT: [task description, where to find PREPARE outputs (e.g., "Read `docs/preparation/{feature}.md`"), plan sections (if any), plan reference]
    - MISSION: [design mission]
    - INSTRUCTIONS: "Preparer task: #{taskId} — read its task file for research decisions and context; `TaskGet` does NOT surface metadata."
    - GUIDELINES: Do not read phase output files yourself or paste their content into the task description. If PREPARE was skipped: pass the plan's Preparation Phase section instead.

**Before next phase**: the [artifact gate](#artifact-gate-prepare-and-architect) (`workflow: architect`, `TaskUpdate(architectTaskId, ...)`), then the shared checks above.

### CODE Phase → `pact-*-coder(s)`

**Always runs.** **S5 Policy Checkpoint (Pre-CODE)**: architecture aligned with project principles? CODE-phase work delegated (the direct-edit lane is standalone only)? S5 non-negotiables safe? Plan sections to pass: "Code Phase", "Implementation Sequence", "Commit Sequence". Coders: `pact-backend-coder`, `pact-frontend-coder`, `pact-database-engineer`, `pact-devops-engineer`.

State the decision: "**Concurrent**: [groupings]", "**Sequential because [specific reason]**: [ordering]", or "**Mixed**: ...". "I'm not sure" defaults to concurrent with S2 coordination.

#### S2 Pre-Dispatch Coordination

Shared files → sequence or assign boundaries; first agent's convention becomes standard; the architect arbitrates technical disagreements; include environment drift (files in `file-edits.json` changed since the last dispatch) on later dispatches ([coordination.md §Environment Drift Detection](../protocols/coordination.md#environment-drift-detection)). Tell concurrent specialists: "You are working concurrently with other specialists. Your scope is [files]. Do not modify files outside your scope."

**Persist `s2_boundaries` and `established_conventions`**: `TaskUpdate(codePhaseTaskId, metadata={"s2_boundaries": {"agent_name": ["file_paths"]}, "established_conventions": {"naming": "...", "patterns": "...", "style": "..."}})`

**Journal event**: After persisting S2 boundaries for concurrent dispatch, write an `s2_state_seeded` event:
```bash
"{plugin_root}/bin/repact-journal-write" --site orchestrate.md --type s2_state_seeded --session-dir '{session_dir}' --stdin <<'JSON'
{"worktree": "{worktree_path}", "agents": ["{agent1}", "{agent2}"], "boundaries": {"{agent1}": ["path/"], "{agent2}": ["path/"]}}
JSON
```

**Include worktree path in all agent prompts**: "You are working in a git worktree at [worktree_path]. All file paths must be absolute and within this worktree. As a teammate, do NOT write a `CLAUDE.md` file in a project directory or a home directory. This covers each route to that write, not only an `Edit` or a `Write` you issue: if a script you run, a command you invoke, or a save path you trigger writes the file, that write is yours. This applies with or without a worktree. The orchestrator manages those files. If your task mentions updating `CLAUDE.md`, flag it in your HANDOFF instead."

**Progress monitoring**: For tasks where mid-flight visibility matters (variety 7+, parallel execution, novel domains), include in the agent prompt: "Send progress signals per teammate-messaging.md §Progress Signals."

**Dispatch coder(s)** — for each coder needed, follow the steps for [Teachback-Gated Dispatch](#teachback-gated-dispatch) with `name="{coder-name}"`, `subagent_type="pact-{coder-type}"`:

- Task A `"{coder-type}: TEACHBACK for {scope}"`; Task B `"{coder-type}: implement {scope}"` (the subjects). Task B fields:
    - CONTEXT: Where to find ARCHITECT outputs (e.g., "Read `docs/architecture/{feature}.md`"), plan sections (if any), plan reference. (NOTE: Do not read the phase output files yourself or paste their content into the task description.)
    - MISSION: [implementation mission]
    - INSTRUCTIONS: "Architect task: #{taskId} — read its task file for design decisions; `TaskGet` does NOT surface metadata." If multiple coders are dispatched concurrently, include peer names: "Your peers on this phase: {other-coder-names}."
    - GUIDELINES:
      - If ARCHITECT was skipped: pass the plan's Architecture Phase section instead.
      - If PREPARE/ARCHITECT were skipped, include: "PREPARE and/or ARCHITECT were skipped based on existing context. Minor decisions (naming, local structure) are yours to make. For moderate decisions (interface shape, error patterns), decide and implement but flag the decision with your rationale in the HANDOFF so it can be validated. Major decisions affecting other components are blockers—don't implement, escalate."
      - "Context budget: grep ranges, not whole files; read test summaries (`tcount`), not logs; build and test the first slice before reading on; write a partial HANDOFF with a `remaining:` list at a clean point, before your context runs out." Above ~15 files, split the unit into build-first slices before dispatch.
      - "Smoke Testing: Run the test suite before completing. If your changes break existing tests, fix them. Import hygiene (pact-coding-standards): before the suite, run `bash {plugin_root}/skills/pact-coding-standards/scripts/lint-check.sh --files` on each .py file you modified; record its verdict line in HANDOFF `produced`. Your tests are verification tests—enough to confirm your implementation works. For HIGH-RISK code (auth, filters, guards, parsers, shared contracts) also write the adversarial boundary tests now; the rest (edge cases, integration, E2E) is TEST phase work."

Spawn multiple coders in parallel (multiple `Agent` calls in one response). Include worktree path and S2 scope boundaries in each task description.

**Dispatch auditor** (concurrent with coders; never skipped without justification). To skip, output on its own line so the user sees it:

> **Auditor skipped**: [justification]

Valid skip reasons: single coder on familiar pattern, variety reassessed below 7, user requested skip, or a full default agent cap (2 at once), where the ONE reviewer's post-artifact audit replaces the auditor. When variety >= 7, 3+ coders run in parallel, code is security-sensitive, or domain has prior architecture drift, auditor dispatch is mandatory unless the cap is full.

1. `TaskCreate(subject="auditor: concurrent quality observation", metadata={"completion_type": "signal"})`
   - Include: architecture doc path, plan path, coder task IDs and scope boundaries
   - Include: "Your observation targets: {coder-names}. Reference chain: architecture doc > plan > dispatch context."
   - Include: "Structural Verification Discipline applies: for any countable or locatable acceptance criterion (file counts, line ranges, touched/untouched paths), verify the claim against `git diff` output before emitting GREEN. HANDOFF prose and commit messages are not ground truth. See STRUCTURAL VERIFICATION DISCIPLINE in your agent body."
2. `TaskUpdate(taskId, owner="auditor")`
3. Spawn the auditor with the canonical dispatch form from the reference, `name="auditor"`, `subagent_type="pact-auditor"`.

The auditor stores its final signal as `metadata.audit_summary` via `TaskUpdate` before marking the task completed. On a RED signal, YOU put the finding on the affected coder's task (`amend_<n>`, kind `correction`) and may pause their work — the auditor reports to you and never directs coders itself. On YELLOW: pass finding to test engineer as focus area. See [coordination.md §Concurrent Audit](../protocols/coordination.md#concurrent-audit) for the full Concurrent Audit Protocol.

When a coder reports stage-ready, write an `amend_<n>` (kind `note`) on the concurrent auditor's task, "coder staged — observe the staged diff now" plus the claims below, then wake it: on every stage-ready, later commits included.

Put in whatever the coder's stage-ready message claimed about the staged work — counts, paths, what it says it did not touch, anything it corrected — labelled as claims for the auditor to verify against the diff, not as findings. Copy the coder's words rather than summarising.

**Before next phase**:
- [ ] Implementation complete
- [ ] All tests passing (full test suite; fix any tests your changes break)
- [ ] **Create atomic commit(s)** of CODE phase work (preserves work before strategic re-assessment). Lead owns commits; specialists stage + `SendMessage` "stage-ready" and wait. A staging specialist should SET the `intentional_wait` task metadata (reason `awaiting_lead_commit`, resolver `lead`) before the stage-ready notify so TeammateIdle hooks do not nag during the commit sequence; CLEAR on the team-lead's commit confirmation (`amend_<n>` kind `commit`, or the completion). See `skills/teammate/references/teammate-waiting.md` §Intentional Waiting for the SET/CLEAR contract.
- [ ] **Journal event**: After each commit, write a `commit` event:
  ```bash
  "{plugin_root}/bin/repact-journal-write" --site orchestrate.md --type commit --session-dir '{session_dir}' --stdin <<'JSON'
  {"sha": "{short_sha}", "message": "{first_line}", "phase": "CODE"}
JSON
  ```
- [ ] **Emit `artifact_paths` for auditor decision-logs (conditional, capture-if-present)** — if a concurrent auditor produced decision-log artifacts, glob `docs/decision-logs/{feature}-*.md`; when ≥1 is present, write a path-only `artifact_paths` event (`workflow=code-auditor`) so they harvest. Unlike PREPARE/ARCHITECT, this is **capture-if-present, not flag-if-missing**: the auditor is optional, so absence is normal — emit nothing and do NOT flag a missing artifact.
  ```bash
  # Best-effort: only when the glob found ≥1 decision-log; if none, skip this emit entirely (auditor is optional).
  "{plugin_root}/bin/repact-journal-write" --site orchestrate.md --type artifact_paths --session-dir '{session_dir}' --stdin <<'JSON'
  {"workflow": "code-auditor", "feature": "{feature}", "paths": ["{absolute_decision_log_path}"]}
JSON
  ```
- [ ] **HANDOFF acceptance**: on receiving each Task-complete `SendMessage`, read the stored HANDOFF once with `repact-payload <id> --digest <d>` and accept on it. If nothing is stored, ask for one in an `amend_<n>` (kind `correction`) before downstream dispatch proceeds. The digest match is the audit — see [completion.md §Read-Trigger Precondition](../protocols/completion.md#read-trigger-precondition) point 4 for a mismatch and the integrity finding.
- [ ] **Concurrent-audit coverage check**: before dispatching TEST, confirm ONE of — the auditor task's file carries `metadata.audit_summary` OR `metadata.audit_summary_authored` (`cat "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/tasks/{team_name}/{taskId}.json" | jq '.metadata | has("audit_summary") or has("audit_summary_authored")'`; `TaskGet` does NOT surface metadata), OR an audit has been dispatched against the committed artifact. If neither holds, put the SHA on the auditor's task (`amend_<n>`, kind `commit`), or dispatch a post-artifact audit, then proceed.
- [ ] **Process coder HANDOFFs** (Ensure the secretary first: see the on-demand secretary step in [bootstrap.md](bootstrap.md#step-2--on-demand-secretary) (TaskCreate → TaskUpdate(owner="secretary") → spawn if absent → SendMessage). Dispatch as soon as the coders have reported; the auditor need not have finished — this harvest writes no Working Memory block, and a later harvest picks up the auditor's HANDOFF):
  ```
  TaskCreate(subject="secretary: harvest pending HANDOFFs",
    description="Harvest HANDOFFs for team {team_name}. Follow the Standard Harvest workflow in your pact-handoff-harvest skill. Report summary when done.")
  TaskUpdate(taskId, owner="secretary")
  ```
  Do not block on completion — TEST phase proceeds in parallel; the harvest is quiet, so the TEST engineer inherits nothing from it.
- [ ] The shared checks above (HANDOFF, blocker, S4 Checkpoint).

**Nested cycles**: a sub-task that needs its own P→A→C→T runs as a nested cycle per [nested-cycle.md](../reference/rePACT/nested-cycle.md); one that only needs independent specialists goes to `/repacted:comPACT`. **Phase re-entry** (via `/repacted:imPACT`): create a new retry phase task, never reopen the completed one ([imPACT.md Phase Re-Entry Task Protocol](imPACT.md#phase-re-entry-task-protocol)). ATOMIZE and CONSOLIDATE (scoped orchestration only) follow [workflows.md §Scoped Phases](../protocols/workflows.md#scoped-phases), with a worktree per sub-scope.

### TEST Phase → `pact-test-engineer`

Plan sections to pass: "Test Phase", "Test Scenarios", "Coverage Targets".

**Dispatch `pact-test-engineer`** — follow the steps for [Teachback-Gated Dispatch](#teachback-gated-dispatch) with `name="test-engineer"`, `subagent_type="pact-test-engineer"`:

- Task A `"test-engineer: TEACHBACK for {feature}"`; Task B `"test-engineer: test {feature}"` (the subjects). Task B fields:
    - CONTEXT: [task description, coder task references (e.g., "Coder tasks: #{id1}, #{id2} — read their task files for implementation decisions and flagged uncertainties"), plan sections (if any), plan reference]
    - MISSION: [testing mission]
    - INSTRUCTIONS: [test-engineer-specific instructions]
    - GUIDELINES: "You own ALL substantive testing: unit tests, integration, E2E, edge cases."

**Before completing**:
- [ ] All tests passing, then the shared checks above (HANDOFF, blocker, S4 Checkpoint)
- [ ] **Agreement verification (L2)**: Before creating PR, verify implementation fulfills original purpose. `SendMessage` to test engineer to verify: "Does the tested implementation match the original requirements?" Background: [completion.md §Agreement Verification](../protocols/completion.md).
- [ ] **Create atomic commit(s)** of TEST phase work (preserves work before strategic re-assessment)
- [ ] **Journal event**: After each commit, write a `commit` event (same pattern as CODE phase commits, with `phase='TEST'`)

Concurrent dispatch: independent suites → several test engineers.

## Agent Stall Detection

Stall indicators, recovery and termination: [coordination.md §Stall Detection](../protocols/coordination.md#stall-detection). Blockers and algedonic signals arrive by `SendMessage`; on HALT, stop every running teammate with the [Lead-Side HALT Fan-Out](../protocols/algedonic.md#lead-side-halt-fan-out) before presenting to the user. 🔴 **The `Agent` tool has no working `resume` parameter. Do not pass one**: it is silently dropped and a fresh agent starts. After a blocker, `SendMessage` a live teammate a self-contained message (the resolution, the task id, what to do), or spawn fresh.

## After All Phases Complete

> **S5 Policy Checkpoint (Pre-PR)**: Before creating PR, verify: "Do all tests pass? Is system integrity maintained? Have S5 non-negotiables been respected throughout?"

1. **Update plan status** (if plan exists): IN_PROGRESS → IMPLEMENTED
2. **Verify all work is committed** — CODE and TEST phase commits should already exist; if any uncommitted changes remain, commit them now
3. **`TaskUpdate`**: Feature task status = "completed" (all phases done, all work committed)
4. **Task-log boundary write** — the feature task is now `completed`, a transition you WITNESSED, so close its project task WITHOUT asking, with the commit as evidence. Run the command rather than editing the log: it validates and appends under the lock.
   ```bash
   python3 "{plugin_root}/bin/repact-task" done <ref> --evidence commit:<sha>
   ```
   Report what you wrote. If the write is refused (already closed, or no task log), report that and carry on.
5. **Run `/repacted:peer-review`** to create PR and get multi-agent review
6. **Present review summary and stop** — use `AskUserQuestion` for merge authorization (S5 policy)
7. **S4 Retrospective** (after user decides): Briefly note—what worked well? What should we adapt for next time? The secretary gathers calibration metrics during HANDOFF processing and will ask you for a brief difficulty assessment. Respond with whether actual difficulty was higher, lower, or about the same as predicted, and which dimensions surprised you.
8. **Save memories from HANDOFFs** (idempotent; Ensure the secretary first: see the on-demand secretary step in [bootstrap.md](bootstrap.md#step-2--on-demand-secretary) (TaskCreate → TaskUpdate(owner="secretary") → spawn if absent → SendMessage).):
   ```
   TaskCreate(subject="secretary: harvest pending HANDOFFs (post-review)",
     description="Harvest HANDOFFs for team {team_name}. Follow the Standard Harvest workflow in your pact-handoff-harvest skill. Then propagate the store into the Working Memory block. Report summary when done.")
   TaskUpdate(taskId, owner="secretary")
   ```
9. **Mid-session consolidation** (multi-feature sessions only): If this is the second or subsequent feature completed in this session, create a consolidation task to merge cross-feature knowledge:
   ```
   TaskCreate(subject="secretary: mid-session consolidation",
     description="Multiple features completed this session. Follow the Consolidation Harvest workflow in your pact-handoff-harvest skill: review memories saved so far, consolidate related entries across features, prune superseded memories. Then propagate the store into the Working Memory block. Report summary when done.")
   TaskUpdate(taskId, owner="secretary")
   ```
   Skip for the first feature in a session — full consolidation happens during `/repacted:wrap-up`.
10. **High-variety audit trail** (variety 11+ only): Save key orchestration decisions, S3/S4 tensions resolved, and lessons learned via the secretary
