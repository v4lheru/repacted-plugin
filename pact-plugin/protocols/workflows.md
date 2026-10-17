# Workflows, variety and phases

## Workflow Selection

| Workflow | When to use | Key idea |
|---|---|---|
| **PACT** (`/repacted:orchestrate`) | Complex or greenfield work | Phased multi-agent orchestration |
| **plan-mode** | Complex work that needs alignment first | Specialist planning consultation, no implementation |
| **comPACT** | Focused, independent tasks | Concurrent specialists, no PACT phases |
| **rePACT** | A complex sub-task inside orchestration | Nested P→A→C→T cycle (single or multi-domain) |
| **imPACT** | Blocked, or iterating on the same failure | Triage: redo a phase? more agents? recoverable? |
| **pause** | PR open, not ready to merge | Consolidate memory, persist state, shut teammates down |

In every workflow teammates have no completion authority (they write the HANDOFF and idle); the lead completes every teammate-owned task and the parent task. The carve-outs are in [Completion Authority](completion.md#completion-authority).

### plan-mode

Planning consultation before implementation: 0 Analyze (scope, select specialists), 1 Consult (specialists in parallel, planning-only mode), 2 Synthesize (resolve conflicts, sequence, assess risk), 3 Present (save `docs/plans/{feature-slug}-plan.md`, await approval). No implementation and no git branch; conflicts are resolved or flagged for the user. After approval the user runs `/repacted:orchestrate {task}`, which references the plan. Recommend comPACT for a trivial task, clarifying questions for unclear requirements, and a PREPARE phase alone when research must come first.

### imPACT

Trigger: blocked, repeated similar errors, or wrong prior-phase output. Check progress-signal history first (converging, exploring or stuck?). Three questions: redo a prior phase? additional agents needed? is the agent recoverable?

| Outcome | When | Action |
|---|---|---|
| Redo prior phase | The issue is upstream in P→A→C→T | Re-delegate that phase |
| Augment present phase | Help needed in the current phase | Re-invoke with more context + parallel agents |
| Nested cycle | The sub-task needs its own cycle | [nested-cycle.md](../reference/rePACT/nested-cycle.md) |
| Terminate agent | Unrecoverable (loop, context exhaustion, stall after resume) | `TaskStop(task_id=taskId)` + `TaskUpdate(taskId, status="completed", metadata={"terminated": true, "reason": "..."})` + fresh spawn with the partial handoff |
| Not truly blocked | No question is "Yes" | Continue with clarified guidance |
| Escalate to user | 3+ imPACT cycles unresolved | Proto-algedonic: ALERT META-BLOCK ([algedonic.md](algedonic.md)) |

Conversation Failure Taxonomy (apply after choosing the outcome): Misunderstanding (wrong output, no errors) → teachback correction; Derailment (loops on one error) → fresh agent, new framing; Discontinuity (lost context) → reconstruct from memory and task files; Absence (thin upstream output) → redo the prior phase.

### comPACT

Concurrent specialists for self-contained tasks, single- or cross-domain, with no shared-file dependencies; use orchestrate when phases must chain. Shorthands: `backend`, `frontend`, `database`, `prepare`, `test`, `architect`, `devops`, `security`, `qa` → `pact-<role>`. Auto-select on a clear task; ask the user on an ambiguous one.

- **Parallel is MANDATORY unless tasks share files or have dependencies** ("API endpoint + DB index" = backend + database in parallel), up to the cap on agents at once in the persona's §11 Lean defaults.
- **Pre-invocation:** reuse the feature worktree or run `/repacted:worktree-setup`; dispatch into the platform's `{team_name}` team; run [Concurrent Dispatch](coordination.md#concurrent-dispatch) when parallel.
- **Specialist instructions:** work from the task description; glance at `docs/plans/`, `docs/preparation/`, `docs/architecture/`; create no new docs; smoke tests only; for parallel or novel work, include "Send progress signals per the teammate skill's messaging reference (teammate-messaging.md, Progress Signals)" in the dispatch prompt.
- **Auditor:** dispatched unless skipped per [Concurrent Audit](coordination.md#concurrent-audit).
- **After completion:** verify the claimed files changed (`git diff --stat`, never the handoff alone), run the tests (failures go back to the specialist), commit atomically, give the secretary a difficulty assessment when asked, then offer PR (`/repacted:peer-review`), `/repacted:pause`, or more work. A blocker goes to `/repacted:imPACT`; escalate to orchestrate on shared-file sequencing or a need for PREPARE/ARCHITECT.

## Variety

The scoring the lead uses at every dispatch (the four dimensions, the workflow bands, the calibration examples, attenuate or amplify) lives in the orchestrator persona §10. This section holds Learning II, the records and the stamp detail.

### Learning II: Pattern-Adjusted Scoring

Before finalizing a score, query pact-memory for `"{domain} orchestration_calibration OR review_calibration"` and `"{domain} blocker OR stall OR rePACT"`. If 5+ memories match a recurring pattern (e.g. "auth tasks consistently underestimated"), bump that dimension by 1, note specialist mismatches, and document "Variety adjusted from {X} to {Y} due to recurring {pattern}". Skip on a project's first session.

### Agent State Model

| State | Indicators | Action |
|---|---|---|
| **Converging** | Progress signals show forward movement | None |
| **Exploring** | Reading, no modifications yet | Normal early; intervene past ~50% of expected duration |
| **Stuck** | No progress signals; stall detection triggers | Send guidance via `SendMessage`; imPACT if unresponsive |

State transitions: Exploring → Converging is normal; Converging → Exploring is concerning; any → Stuck needs intervention. Request progress signals in dispatches where mid-flight visibility matters (variety 7+, parallel, novel domains).

### Variety Calibration Record

At workflow completion the secretary gathers the metrics during HANDOFF processing, asks the lead for a difficulty assessment (higher, lower, same) and saves the record to pact-memory with entities `orchestration_calibration` AND `{domain}` (Learning II queries need both). Drift above 2 in any dimension is noted as significant.

```
CalibrationRecord:
  task_id: str                    # feature task id
  domain: str                     # top-level domain, e.g. "auth"
  initial_variety_score: int      # at orchestration start (4-16)
  actual_difficulty_score: int    # post-hoc (4-16)
  dimensions_that_drifted:        # - dimension: novelty|scope|uncertainty|risk; predicted: 1-4; actual: 1-4
  blocker_count: int              # imPACT cycles
  phase_reruns: int               # phases redone
  specialist_fit: str | null      # good | undermatched | overmatched | null
  timestamp: str                  # ISO 8601
```

#### Per-Dispatch Variety Stamping

Every Task B gets `metadata.variety` at `TaskCreate` time, scored afresh for THIS dispatch (not inherited from, nor capped by, the feature score):

```
{"variety": {"novelty": 1-4, "novelty_rationale": "<1 sentence: the shape of this dispatch's novelty>",
             "scope": 1-4, "scope_rationale": "...", "uncertainty": 1-4, "uncertainty_rationale": "...",
             "risk": 1-4, "risk_rationale": "...", "total": 4-16}}
```

- **The teammate reads the rationales before it works.** Describe only the SHAPE of the work: novelty, breadth, unknowns, exposure. Do not write the dispatch's hypothesis, its expected answer, or the reasoning that produced it. Where a dispatch's value depends on the teammate NOT knowing something, the rationales must not disclose it.
- **Write-time check:** a rationale describes the WORK (what this dispatch must do, know, or not get wrong), not the topic. "A wrong path writes the store somewhere unintended" is the reviewed change's risk and FAILS on a review; "the review's own risks are a probe that writes to the live store, and a missed finding" PASSES. Four rationales, one per dimension, make cargo-culting visible.
- **`total` is the canonical key.** The band resolver also tolerates `score`, top-level `variety_score`, or the dimension sum, but a resolvable `total` is taken as stated even when it disagrees with the dimensions; nothing reports that disagreement.
- **Missing stamp:** the resolver inherits the band from the single stamped parent Task B blocks (else `unresolvable`), a read-time safety net only. The dispatch-wiring write (`owner` + `addBlockedBy`) REFUSES an unstamped Task B (`PACT_DISPATCH_VARIETY_MODE` defaults to `deny`; `warn`/`shadow` opt down); a present-but-malformed stamp is a post-write advisory.
- **Context budget:** the stamp also sets the teammate's context lines (`hooks/shared/context_ledger.py`, bytes of estimated context). Default: advise 300 KB, stop 500 KB. Large (`scope` >= 3 or `risk` >= 4): advise 500 KB, stop 800 KB. To set it yourself, write `metadata.context_budget_kb` (a number, clamped to 300..1000) beside `variety` on Task B: it is the stop line, advise is 60% of it, and it wins over the stamp. Three notices, once each: advise (to you), stop (to the teammate and you: no new work, test and fix what is written, then HANDOFF with `remaining:`), and hard at stop x 1.25 (HANDOFF now). Your own session keeps the default.
- **Journal mirror:** every stamping site also writes a `variety_assessed` event for that task with the four scores and total (no rationales) and top-level `"scope": "dispatch"`. Feature-level events carry no `scope`; readers key on that difference.

#### variety_acknowledgment — Teammate Verification Workflow

The teammate peer-reviews the lead's scoring in its teachback:

```
"variety_acknowledgment": {"rationale_articulates_this_dispatch": "yes" | "no" | "concern",
                           "concern": "<required when value != 'yes'; names the smell>"}
```

> **Where a dispatch's value depends on the teammate NOT knowing something, stamp every `*_rationale` as `WITHHELD: ignorance-dependent dispatch` and nothing else.** The scores stay real, and the teammate still files `concern` naming the withholding.

1. The teammate reads Task B's `metadata.variety` (via `Task A.blocks[0]`), scores and rationales. The stamp shares a task file with the mission, so no read order is imposed.
2. It judges each rationale against THIS dispatch and records `yes`, `no` (cargo-culted or wrong) or `concern` (reservation), naming the smell.

**Lead side** ([Teachback Review](completion.md#teachback-review)): on `yes`, accept normally. On `no`/`concern`:
- **EXCEPTION — a withheld stamp**: when the rationales read `WITHHELD: ignorance-dependent dispatch` and the `concern` names that withholding, take NEITHER option: accept and leave the stamp. This overrides the preference for orchestrator-side correction: the flag is correct BY CONSTRUCTION, and re-stamping writes the hypothesis the withholding exists to keep out of the task.
- *Orchestrator-side correction* (preferred when the flag is right): re-stamp `metadata.variety` on Task B, then accept.
  ⚠️ Re-send the FULL `variety` object in ONE `TaskUpdate`: a write that names `variety` REPLACES the whole object and erases each field you omit, with no error. Then read the task file back and enumerate the keys.
- *Stamp stands* (the flag is wrong): put why in an `amend_<n>` ruling on Task A, then accept. Never reject a teachback only to disagree about a score.

Flags persisting 3+ cycles are an imPACT META-BLOCK.

#### Variety Acknowledgment Signal

At wrap-up the secretary aggregates acknowledgments for the current arc: more than 20% `no`/`concern` flags the scoring as possibly miscalibrated; a single `no` on a load-bearing dispatch is surfaced on its own. An acknowledgment whose `concern` carries the literal `WITHHELD: ignorance-dependent dispatch` is excluded from both terms of the rate and from the acute list, and the exclusion must be applied while `concern` is still in hand. Q5 and Q6 are scoped to the current arc: `arc_start` is the latest non-dispatch `variety_assessed.ts` for the current `feature_task_id`, and every journal read passes `--since arc_start` (timestamp-parsed, inclusive, falling back to the whole journal when no match exists).

## Phase Transitions

**Every phase handoff states** what was produced (paths), the key decisions, and what the next agent needs. Before completing a phase, consider security, performance, accessibility and observability (awareness, not a checklist). For complex features, coders may sanity-check the architecture before CODE.

| Test type | Owner |
|---|---|
| Smoke tests | Coders: "Does it compile? Does it run? Does the happy path not crash?" |
| Unit, integration, E2E, edge and adversarial | Test engineer, after CODE; 80%+ meaningful coverage of critical paths |

### CODE → TEST Handoff

Coders hand off to the lead, who passes it to the test engineer. These are PROSE labels. When the same HANDOFF is stored in task metadata, use the canonical `metadata.handoff` keys, in this order: `produced`, `decisions`, `reasoning_chain`, `uncertainty`, `integration`, `open_questions`. Never convert a display label into a key — `Key decisions` is the label, `decisions` is the key.

```
1. Produced: files created/modified
2. Key decisions: with rationale and assumptions that could be wrong
3. Reasoning chain (recommended): "X because Y, which required Z"
4. Areas of uncertainty (PRIORITIZED): [HIGH] could break in production (MUST be tested) / [MEDIUM] should be tested / [LOW] tester's discretion
5. Integration points
6. Open questions
```

Items 1-2 and 4-6 are required; with nothing uncertain, state "No areas of uncertainty flagged". The test engineer decides how to test, but every HIGH area gets explicit test cases (a skip is documented), and findings are reported as GREEN/YELLOW/RED.

### S4 Checkpoints

After PREPARE, ARCHITECT and CODE, on unexpected complexity, or when the user asks to pause and assess (a days horizon: the milestone, not the minute):

1. **Environment Change**: new requirements, invalidated constraints, changed dependencies?
2. **Model Divergence**: assumptions wrong, estimates off, risks materialized?
3. **Plan Viability**: continue, adapt, or escalate to the user?
4. **Shared Understanding**: does your reading match the specialist's handoff? (At final gates, confirm per [Agreement Verification](completion.md#teachback-review); at intermediate ones the next teachback verifies.)

Outcomes: all clear → continue; minor drift → note and continue; significant change → pause, maybe re-run a phase; fundamental shift → escalate (S5). Silent unless an issue appears; then surface `**S4 Checkpoint** [Phase→Phase]: Environment / Model / Plan / Agreement`. An auditor RED prompts a checkpoint.

**Environment model:** for variety 11+ (recommended 7-10), write `docs/preparation/environment-model-{feature}.md` during PREPARE (stack, external dependencies, constraints, acknowledged unknowns, invalidation triggers) and update it when an assumption breaks; it is the baseline the checkpoint compares against.

## Scope Detection

After PREPARE (or an approved plan's Preparation section), before ARCHITECT, score whether the task splits into sub-scopes:

| Signal | Points |
|---|---|
| Distinct domain boundaries (2+ independent services, stacks or specialist areas) | 2 |
| Non-overlapping work areas (no shared files or components) | 2 |
| High specialist count (4+ across domains) | 1 |
| Prior complexity flags in pact-memory | 1 |
| Counter-signal: shared data models across domains | −1 |
| Counter-signal: small total scope despite several domains | −1 |

Decompose at **score ≥ 3** (floor 0); if only one sub-scope emerges, stay single-scope. With neither PREPARE output nor a plan: variety Scope ≥ 3 forces PREPARE; below 3 skips detection. Sub-scopes never re-run detection (the 1-level nesting limit is the safety net); comPACT and a manual nested cycle bypass it.

**Tiers.** Manual (nested cycle, always available). Confirmed (default): propose via S5 framing (`📐 Scope Change: Multi-scope task detected`, options A decompose / B single scope / C adjust boundaries); A or C → scope contracts, then ATOMIZE; B → single scope. Autonomous: only when BOTH strong signals fire, no counter-signal, and the injected **PACT Runtime Config** shows `Autonomous scope detection: ON` (`PACT_AUTONOMOUS_SCOPE_DETECTION`); output `Scope detection: Multi-scope (autonomous) — decomposing into [scope list]`.

## Scoped Phases

When decomposition is confirmed, ATOMIZE and CONSOLIDATE replace ARCHITECT and CODE.

**Scope contract** (generated from PREPARE output, ~5-10 lines, WHAT not HOW):

```
Scope Contract: {scope-name}
Identity: scope_id (kebab-case), parent_scope, executor (rePACT)
Deliverables: expected paths and non-file artifacts
Interfaces: exports (to siblings) / imports (from siblings)
Constraints: shared_files (must NOT modify; a file in several scopes is owned by the one changing it most), conventions
```

The sub-scope HANDOFF appends **Contract Fulfillment**: each deliverable ✅ → artifact or ❌ → reason, the exports and imports actually used, and deviations with rationale.

**ATOMIZE.** Create a worktree per scope (`/repacted:worktree-setup`, branch `feature-X--{scope_id}`), persist `TaskUpdate(scopeTaskId, metadata={"scope_contract": {...}, "worktree_path": "...", "nesting_depth": 1})`, and run each scope per [nested-cycle.md](../reference/rePACT/nested-cycle.md), concurrently unless files are shared. The executor reads its inputs from the task file (`TaskGet` does NOT surface metadata: `cat .../tasks/{team_name}/{taskId}.json | jq .metadata.<key>`) and returns the standard HANDOFF plus fulfillment, commits, and `completed` (non-happy paths use `{"stalled": true}` / `{"blocked": true}` metadata). A scope failure is isolated to that scope (imPACT), but a HALT from any scope stops ALL scopes, preserving their work. Before CONSOLIDATE: every scope done with fulfillment, S4 checkpoint.

**CONSOLIDATE.** Recover each scope's contract and worktree from its task file (or, if the task store is drained, from the latest `task_metadata_snapshot` in the session journal). Merge each scope branch with `git merge --no-ff` (a conflict is an ALERT: a `shared_files` violation), clean up the worktrees, then in parallel have `pact-architect` verify cross-scope contract compatibility (exports match imports, undelivered items) and `pact-test-engineer` run cross-scope integration tests. Failures route through imPACT. Commit, then S4 checkpoint.

## S3/S4 Tension

**S3** executes the plan (agents converging, exploring or stuck?); **S4** asks whether it is the right thing. Enter S4 at task start, on a blocker and at phase ends (plan still viable?); name your mode on big decisions.

S3 (execute now) and S4 (adapt) pull against each other: schedule vs quality, execute vs investigate, commit vs adapt, efficiency vs safety. "We're behind, skip PREPARE" is S3 pushing; "requirements seem unclear" is S4 pulling. Name the tension, state both paths' gains and risks, weigh them against project values and the user's stated preferences, then decide and document, or escalate to the user with `⚖️ **S3/S4 Tension**` framing ([S5 Escalation](algedonic.md#s5-escalation)) when unclear.

## Specialist Autonomy

Specialists may adjust their approach, request context through the lead, recommend scope changes and apply domain expertise without micro-management. They must escalate when a discovery contradicts the architecture, scope changes by more than 20%, security or policy implications appear, or they need a change in another specialist's area. In parallel work they respect assigned boundaries, adopt the first agent's conventions and report conflicts at once.

**Nested PACT:** a specialist may declare "Invoking nested PACT for {sub-task}" for a sub-task with its own uncertainty or several downstream consumers, run a mini P→A→C→T within its domain (skipping unneeded phases), and report it in the handoff. Nesting is limited to 1 level; cross-domain needs go to the lead; algedonic signals from a nested cycle still go directly to the user. The lead can start it upfront: single domain (`backend "implement rate limiting"`) or multi-domain (`"implement audit logging sub-system"`).
