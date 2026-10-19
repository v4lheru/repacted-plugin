---
description: Build a feature with a team of AI agents
argument-hint: [e.g., implement feature X]
---
Orchestrate specialist PACT agents through the PACT workflow to address: $ARGUMENTS

On demand: [phase-skip-gates.md](../reference/orchestrate/phase-skip-gates.md) (before skipping any phase), [scoped-orchestration.md](../reference/orchestrate/scoped-orchestration.md) (scope detection after PREPARE, nested cycles), [assessment-and-signals.md](../reference/orchestrate/assessment-and-signals.md) (scoring complexity, before a CODE dispatch, or on a signal or blocker).

---

## Tasks

Your own tasks, written with `TaskCreate` and `TaskUpdate`: the feature task "{verb} {feature}" (`in_progress`) and the phase tasks "PREPARE/ARCHITECT/CODE/TEST: {feature-slug}", each blocked by the one before. When decomposition fires after PREPARE, ARCHITECT and CODE are skipped (`decomposition_active`) and ATOMIZE / CONSOLIDATE phases take their place (the scoped reference). A specialist's work task is made by `repact dispatch`, never by hand.

Per phase: set its task `in_progress`, dispatch, accept each result, complete the phase task, and journal each start, completion or skip:
```bash
"{plugin_root}/bin/repact-journal-write" --site orchestrate.md --type phase_transition --session-dir '{session_dir}' --stdin <<'JSON'
{"phase": "{PHASE}", "status": "{started|completed|skipped}", "skip_reason": "{reason or empty}"}
JSON
```

> ⚠️ **Heredoc-stdin contract for journal events**: every journal write uses `--stdin <<'JSON' ... JSON` (quoted delimiter) so substituted values pass through unexpanded; substitute only JSON-valid strings (escape `\"`, `\\`, control characters).

### Dispatch

Every specialist goes out through one command (persona §11):

```
"{plugin_root}/bin/repact" dispatch --name <name> --agent pact-<type> --subject "<what>" --brief-file <mission file> \
  --variety N,S,U,R --kind fix|feature|refactor|config|docs --branch <branch> \
  [--feature {feature}] [--acceptance docs/{feature}/acceptance.md] [--ref T-<id>]...
```

Then run the `Agent(...)` call it prints, exactly. Several independent specialists: one dispatch each, then all their `Agent` calls in one response. The mission file (CONTEXT / MISSION / INSTRUCTIONS / GUIDELINES) lives in your scratchpad; the brief adds the worktree, the pinned acceptance commands, the test line and the hand-in steps, so never repeat those. Each phase below gives only its mission fields. Read the repo's `.claude/pact-dispatch-notes.md` once, if present, and put its relevant lines in each mission. The teachback, accepting, rejecting and amending follow persona §11-12: `repact accept <id>`, `repact reject <id> --reason "<text>"`, `repact amend <id> --text "<ruling>"`, and `repact inspect <id>` for the receipt. Stop each specialist once its task is accepted.

**Before the next phase**, every phase checks: every task of the phase accepted; a blocker reported → [blocker triage](../reference/decide/blocker-triage.md); the plan checkpoint ([workflows.md §S4 Checkpoints](../protocols/workflows.md#s4-checkpoints)): environment stable? model aligned? plan still viable? (complexity 7+: optionally ask the secretary for a pattern check, see [pact-orchestrator §Memory Management](../agents/pact-orchestrator.md#memory-management)).

#### Artifact gate (PREPARE and ARCHITECT)

For a phase that **ran** (not skipped), its artifact MUST exist on disk and be non-empty before you complete the phase task: PREPARE at `docs/preparation/{feature}.md` (plus any other produced files, e.g. `environment-model-{feature}.md`); ARCHITECT at `docs/architecture/{feature}.md` (fix-up work on someone else's repo: `{session_dir}/architecture/{feature}.md`). With two or more CODE coders on one interface, the doc declares one contract test per interface as `Contract:` lines. A missing or empty artifact is a **failure to investigate**: do NOT complete the phase; find out why (wrong location, nothing produced, a different slug) and resolve it. Then glob the worktree's `docs/preparation/` or `docs/architecture/` (or `{session_dir}/architecture/`) yourself, never from a HANDOFF, and journal the full absolute paths, so the artifact is recoverable without the HANDOFF. Only after that, complete the phase task. A **skipped** phase is exempt.
```bash
# Emit only when ≥1 non-empty path is found; an empty result is the missing-artifact case, not an event.
"{plugin_root}/bin/repact-journal-write" --site orchestrate.md --type artifact_paths --session-dir '{session_dir}' --stdin <<'JSON'
{"workflow": "{prepare|architect}", "feature": "{feature}", "paths": ["{absolute_path_1}", "{absolute_path_2}"], "task_id": "{phase_task_id}"}
JSON
```

**Skipped phases** go straight to `completed`: `TaskUpdate(phaseTaskId, status="completed", metadata={"skipped": true, "skip_reason": "{reason}"})`, with reason `"plan_section_complete"` (the plan section passed its completeness check), `"structured_gate_passed"` (every structured question answered concretely) or `"decomposition_active"` (sub-scopes handle this phase in a nested cycle).

---

## Before Starting

Score complexity per [workflows.md §Variety](../protocols/workflows.md#variety) (novelty, scope, uncertainty, risk, 1-4 each): 4-6 → offer comPACT; 7-10 → orchestrate; 11-14 → plan-mode first; 15-16 → a research spike. One line, e.g. `Complexity: 8 — orchestrate with all phases`. Store it on the feature task, `TaskUpdate(featureTaskId, metadata={"variety": {"novelty": N, "scope": N, "uncertainty": N, "risk": N, "total": N}})`, and journal it (the feature-level row marks the start of this piece of work for wrap-up):
```bash
"{plugin_root}/bin/repact-journal-write" --site orchestrate.md --type variety_assessed --session-dir '{session_dir}' --stdin <<'JSON'
{"task_id": "{feature_task_id}", "variety": {"novelty": N, "scope": N, "uncertainty": N, "risk": N, "total": N}}
JSON
```
After a compaction, `repact inspect` shows the feature; the score is in the feature task file's `metadata.variety` (`TaskGet` does NOT surface metadata). With no score, the hard skip gates cannot be judged, so every phase runs.

**Cost preview**: before each phase's first dispatch, print the line of `python3 "{plugin_root}/bin/repact-cost" --preview <N>` (N specialists) to the user as it is.

> **Task-log boundary write**: starting the feature is a transition you WITNESSED. Mark its project task `active`, linked to the feature task, WITHOUT asking, once per ref the feature owns, and pass the same refs to each dispatch as `--ref`; report the write or refusal and carry on.
>
> ```bash
> python3 "{plugin_root}/bin/repact-task" start <ref> --ptask {team_name}/<feature task id>
> ```

At complexity 4-6, offer the choice:

```
AskUserQuestion(
  question: "This task appears routine. Which workflow?",
  options: ["comPACT (Recommended)", "Full orchestrate"]
)
```

## Execution

**Dispatch concurrently unless blocked.** Without a specific file conflict or data dependency, independent specialists go out together, in every phase.

1. **Worktree**: reuse the feature's, else `/repacted:worktree-setup`; `--branch` on each dispatch makes or reuses the specialist's own.
2. **Plan**: check `docs/plans/`: `/repacted:orchestrate` on a PENDING APPROVAL or APPROVED plan sets it IN_PROGRESS; ask on BLOCKED, IN_PROGRESS or SUPERSEDED.

**Phase completed**: after its `phase_transition` row, write the `checkpoint`:
```bash
"{plugin_root}/bin/repact-journal-write" --site orchestrate.md --type checkpoint --session-dir '{session_dir}' --stdin <<'JSON'
{"phase": "{PHASE}", "completed_phases": [...], "active_agents": [{"name": "...", "task_id": "...", "status": "..."}], "variety": VARIETY_DICT_OR_NULL, "pending_phases": [...], "safe_to_retry": true}
JSON
```

## Phase Skip Decision

Phases run by default; a skip must be earned. PREPARE or ARCHITECT is skipped only when all three layers pass (hard gates on the score, plan completeness, structured analysis): **load [phase-skip-gates.md](../reference/orchestrate/phase-skip-gates.md) before any skip**. CODE always runs; TEST skips only on all four of its conditions. When in doubt, the phase runs.

### PREPARE → `pact-preparer`

Plan sections to pass: "Preparation Phase", "Open Questions > Require Further Research". `repact dispatch --name preparer --agent pact-preparer --subject "research {feature}" --kind docs ...`, mission:
- CONTEXT: the task description, plan sections (if any)
- MISSION: the research mission
- INSTRUCTIONS: preparer-specific instructions
- GUIDELINES: "Reference the approved plan at `docs/plans/{slug}-plan.md` for full context."

Several independent research areas → several preparers. **Before next phase**: the [artifact gate](#artifact-gate-prepare-and-architect) (`workflow: prepare`), then the shared checks. Then run scope detection and the PREPARE→ARCHITECT coupling ([scoped-orchestration.md](../reference/orchestrate/scoped-orchestration.md)).

### ARCHITECT → `pact-architect`

Plan sections to pass: "Architecture Phase", "Key Decisions", "Interface Contracts". `repact dispatch --name architect --agent pact-architect --subject "design {feature}" --kind docs ...`, mission:
- CONTEXT: the task description, where the PREPARE output is (e.g. "Read `docs/preparation/{feature}.md`"), plan sections and plan reference
- MISSION: the design mission
- INSTRUCTIONS: "Preparer task: #{taskId}; `repact inspect {taskId}` shows its decisions."
- GUIDELINES: do not read phase output files yourself or paste them in. PREPARE skipped: pass the plan's Preparation Phase section instead.

**Acceptance criteria**: for testable requirements the test engineer writes `docs/{feature}/acceptance.md` (here or in plan-mode; one `AC-<n>: <criterion> `<command>`` per line) and the failing tests before CODE. Each coder's dispatch then takes `--acceptance docs/{feature}/acceptance.md`, which pins the commands, and `--ref AC-<n>` for each AC it owns; `repact inspect --acceptance docs/{feature}/acceptance.md` shows each AC with its latest run.

**Before next phase**: the [artifact gate](#artifact-gate-prepare-and-architect) (`workflow: architect`), then the shared checks.

### CODE → `pact-*-coder(s)`

**Always runs.** **Rules checkpoint (before CODE)**: architecture in line with the project's principles? CODE work delegated (the direct-edit lane is standalone only)? The rules that do not bend safe? Plan sections to pass: "Code Phase", "Implementation Sequence", "Commit Sequence". Coders: `pact-backend-coder`, `pact-frontend-coder`, `pact-database-engineer`, `pact-devops-engineer`.

State the decision: "**Concurrent**: [groupings]", "**Sequential because [specific reason]**: [ordering]", or "**Mixed**: ...". Unsure → concurrent, with coordination.

#### Coordination before dispatch

Shared files → sequence them or split the boundaries; the first agent's convention becomes the standard; the architect settles technical disagreements; on later dispatches name the files in `file-edits.json` changed since the last one ([coordination.md §Environment Drift Detection](../protocols/coordination.md#environment-drift-detection)). Tell concurrent specialists: "You are working concurrently with other specialists. Your scope is [files]. Do not modify files outside your scope." Persist the split: `TaskUpdate(codePhaseTaskId, metadata={"s2_boundaries": {"agent_name": ["file_paths"]}, "established_conventions": {"naming": "...", "patterns": "...", "style": "..."}})`, then journal it:
```bash
"{plugin_root}/bin/repact-journal-write" --site orchestrate.md --type s2_state_seeded --session-dir '{session_dir}' --stdin <<'JSON'
{"worktree": "{worktree_path}", "agents": ["{agent1}", "{agent2}"], "boundaries": {"{agent1}": ["path/"], "{agent2}": ["path/"]}}
JSON
```

**Dispatch each coder** with `repact dispatch --name {coder-name} --agent pact-{coder-type} --subject "implement {scope}" --kind feature|fix|refactor ...`, mission:
- CONTEXT: where the ARCHITECT output is (e.g. "Read `docs/architecture/{feature}.md`"), plan sections, plan reference. (Do not read phase output files yourself or paste them in.)
- MISSION: the implementation mission
- INSTRUCTIONS: "Architect task: #{taskId}; `repact inspect {taskId}` shows its decisions." Concurrent coders: "Your peers on this phase: {other-coder-names}.", plus the contract tests it implements or consumes; the join is done when they pass in a recorded run. "Do NOT write a `CLAUDE.md` file anywhere, by any route (a script or save path you trigger counts); if your task asks for one, flag it in your HANDOFF." Where mid-flight visibility matters (complexity 7+, parallel, a novel domain): "Send progress signals per teammate-messaging.md §Progress Signals."
- GUIDELINES:
  - ARCHITECT skipped: pass the plan's Architecture Phase section instead. PREPARE or ARCHITECT skipped: "Minor decisions (naming, local structure) are yours. For moderate ones (interface shape, error patterns), decide, implement and flag the decision with its reason in the HANDOFF. Major decisions affecting other components are blockers: don't implement, escalate."
  - "Context budget: grep ranges, not whole files; read test summaries (`tcount`), not logs; build and test the first slice before reading on; at a clean point before your context runs out, hand in a partial HANDOFF with a `remaining:` list." Above ~15 files, split the unit into build-first slices before dispatch.
  - "Smoke testing: run the suite before handing in and fix what your changes break. Import hygiene (pact-coding-standards): before the suite, run `bash {plugin_root}/skills/pact-coding-standards/scripts/lint-check.sh --files` on each .py file you modified; record its verdict line in HANDOFF `produced`. Your tests confirm your implementation works. For HIGH-RISK code (auth, filters, guards, parsers, shared contracts) also write the adversarial boundary tests now; the rest (edge cases, integration, E2E) is TEST phase work."

**Auditor** (concurrent with the coders). Mandatory at complexity 7+, with 3+ parallel coders, security-sensitive code or past architecture drift, unless the default agent cap (2 at once) is full; then the ONE reviewer's audit of the finished work replaces it. Any other skip is stated on its own line:

> **Auditor skipped**: [justification]

The auditor is a signal task, not a `repact dispatch`: it completes its own task with `metadata.audit_summary`. `TaskCreate(subject="auditor: concurrent quality observation", metadata={"completion_type": "signal"})` with a description naming the architecture doc, the plan, the coder task ids and scope boundaries, "Your observation targets: {coder-names}. Reference chain: architecture doc > plan > dispatch context." and "Structural Verification Discipline applies: verify every countable or locatable claim against `git diff` before GREEN; HANDOFF prose and commit messages are not ground truth."; then `TaskUpdate(taskId, owner="auditor")` and spawn it as a background `pact-auditor` named `auditor`, with a prompt of at most 800 chars naming its task id and `TaskList` (persona §11, the spawn not made by `repact dispatch`). When a coder hands in, `SendMessage` the auditor "#<id> handed in, observe the diff now" plus the coder's own words about what it did and did not touch, labelled as claims to verify. On RED, put the finding on the coder's task (`repact amend <id> --text "<finding>" --kind correction`) and pause that coder if needed; the auditor reports to you and never directs coders. On YELLOW, pass it to the test engineer as a focus area. Protocol: [coordination.md §Concurrent Audit](../protocols/coordination.md#concurrent-audit).

**Before next phase**:
- [ ] Every coder's task accepted (`repact accept <id>`; it runs the tests' evidence and the pinned acceptance commands).
- [ ] The full suite passes; fix what the changes broke.
- [ ] **Commits are yours**: compare `git diff --cached --stat` with each HANDOFF's `produced`, commit, and journal each commit:
  ```bash
  "{plugin_root}/bin/repact-journal-write" --site orchestrate.md --type commit --session-dir '{session_dir}' --stdin <<'JSON'
  {"sha": "{short_sha}", "message": "{first_line}", "phase": "CODE"}
JSON
  ```
- [ ] **Auditor decision logs** (only when present): glob `docs/decision-logs/{feature}-*.md`; for one or more, journal their paths. None is normal; emit nothing.
  ```bash
  "{plugin_root}/bin/repact-journal-write" --site orchestrate.md --type artifact_paths --session-dir '{session_dir}' --stdin <<'JSON'
  {"workflow": "code-auditor", "feature": "{feature}", "paths": ["{absolute_decision_log_path}"]}
JSON
  ```
- [ ] **Audit coverage**: before TEST, the auditor task's file carries `metadata.audit_summary` or `metadata.audit_summary_authored` (`jq '.metadata | has("audit_summary") or has("audit_summary_authored")' "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/tasks/{team_name}/{taskId}.json"`; `TaskGet` does NOT surface metadata), or an audit of the committed work has been dispatched. If neither, `SendMessage` the auditor the SHA, or dispatch an audit of the commit, then proceed.
- [ ] **Harvest the HANDOFFs**: ensure the secretary first (the on-demand step in [bootstrap.md](bootstrap.md#step-2--on-demand-secretary)), then:
  ```
  TaskCreate(subject="secretary: harvest pending HANDOFFs",
    description="Harvest HANDOFFs for team {team_name}. Follow the Standard Harvest workflow in your pact-handoff-harvest skill. Report summary when done.")
  TaskUpdate(taskId, owner="secretary")
  ```
  Do not wait for it: TEST runs alongside, and this harvest writes no Working Memory block.
- [ ] The shared checks above.

**Nested cycles**: a sub-task that needs its own P→A→C→T runs as a nested cycle per [nested-cycle.md](../reference/rePACT/nested-cycle.md); one that only needs independent specialists goes to `/repacted:comPACT`. **Phase re-entry** (after blocker triage): a new retry phase task, never the completed one reopened. ATOMIZE and CONSOLIDATE (scoped orchestration only) follow [workflows.md §Scoped Phases](../protocols/workflows.md#scoped-phases), with a worktree per sub-scope.

### TEST → `pact-test-engineer`

Plan sections to pass: "Test Phase", "Test Scenarios", "Coverage Targets". `repact dispatch --name test-engineer --agent pact-test-engineer --subject "test {feature}" --kind feature ...` (with `--acceptance` when the feature has one), mission:
- CONTEXT: the task description, the coder tasks ("Coder tasks: #{id1}, #{id2}; `repact inspect <id>` shows their decisions and flagged uncertainties"), plan sections and plan reference
- MISSION: the testing mission
- INSTRUCTIONS: test-engineer-specific instructions
- GUIDELINES: "You own ALL substantive testing: unit, integration, E2E, edge cases." With acceptance.md: "The ACs are the definition of done; test beyond them."

Independent suites → several test engineers. **Before completing**:
- [ ] Tests pass and the test engineer's task is accepted, then the shared checks.
- [ ] **Agreement check**: before the PR, the implementation fulfils the original purpose; with acceptance.md, `repact inspect --acceptance docs/{feature}/acceptance.md --task <id>` shows each AC green after a red run. Ask the test engineer: "Does the tested implementation match the original requirements?"
- [ ] Commit the TEST work and journal each commit (as in CODE, with `"phase": "TEST"`).

## Agent Stall Detection

Silence is not a stall: look at `repact inspect` first. Stall signs, recovery and termination: [coordination.md §Stall Detection](../protocols/coordination.md#stall-detection). Blockers and stop signals arrive by `SendMessage` or as a subagent's final message; on a stop, halt every running agent before you present it to the user ([stop signals](../protocols/stop-signals.md#stopping-every-teammate)). 🔴 **The `Agent` tool has no working `resume` parameter. Do not pass one**: it is dropped and a fresh agent starts. After a blocker, send a live agent one self-contained message (the resolution, the task id, what to do), or dispatch fresh.

## After All Phases Complete

> **Rules checkpoint (before the PR)**: do all tests pass? Is the system's integrity kept? Were the rules that do not bend respected throughout?

1. **Plan status** (if a plan exists): IN_PROGRESS → IMPLEMENTED.
2. **Everything committed**: CODE and TEST commits exist; commit anything left.
3. **Feature acceptance** when the feature has several tasks: merge their branches into `.worktrees/{feature}`, run its acceptance commands and the suite there with `"${CLAUDE_PLUGIN_ROOT}/bin/tcount" <cmd>` (this plugin's tcount; a bare one can be another install), then `repact accept --feature {feature}`. Then `TaskUpdate(featureTaskId, status="completed")`.
4. **Task-log boundary write**: the feature task is now `completed`, a transition you WITNESSED, so close its project task WITHOUT asking, with the commit as evidence (the command validates and appends under the lock; never edit the log by hand):
   ```bash
   python3 "{plugin_root}/bin/repact-task" done <ref> --evidence commit:<sha>
   ```
   Report what you wrote; a refusal (already closed, or no task log) is reported, then carry on.
5. **`/repacted:peer-review`** opens the PR and gets it reviewed.
6. **Present the review and stop**: the merge is the user's, through `AskUserQuestion`.
7. **Retrospective** (after the user decides): what worked, what to change. The secretary asks for a difficulty check during its harvest: say whether it was harder, easier or as predicted, and which dimensions surprised you.
8. **Save the HANDOFFs to memory** (idempotent; ensure the secretary first, as above):
   ```
   TaskCreate(subject="secretary: harvest pending HANDOFFs (post-review)",
     description="Harvest HANDOFFs for team {team_name}. Follow the Standard Harvest workflow in your pact-handoff-harvest skill. Then propagate the store into the Working Memory block. Report summary when done.")
   TaskUpdate(taskId, owner="secretary")
   ```
9. **Second or later feature this session only**: merge knowledge across features.
   ```
   TaskCreate(subject="secretary: mid-session consolidation",
     description="Multiple features completed this session. Follow the Consolidation Harvest workflow in your pact-handoff-harvest skill: review memories saved so far, consolidate related entries across features, prune superseded memories. Then propagate the store into the Working Memory block. Report summary when done.")
   TaskUpdate(taskId, owner="secretary")
   ```
   The first feature skips it; `/repacted:wrap-up` consolidates in full.
10. **Complexity 11+ only**: save the key decisions, the tensions you settled and the lessons through the secretary.
