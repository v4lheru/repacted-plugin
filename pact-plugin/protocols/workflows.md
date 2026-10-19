# Workflows, complexity and phases

## Workflow Selection

| Workflow | When to use | Key idea |
|---|---|---|
| **PACT** (`/repacted:orchestrate`) | Complex or greenfield work | Phased multi-agent orchestration |
| **plan-mode** | Complex work that needs alignment first | Specialist planning consultation, no implementation |
| **comPACT** | Focused, independent tasks | Concurrent specialists, no PACT phases |
| **rePACT** | A complex sub-task inside orchestration | Nested P→A→C→T cycle (single or multi-domain) |
| **Blocker triage** | Blocked, or iterating on the same failure | Redo a phase? more agents? recoverable? ([blocker-triage.md](../reference/decide/blocker-triage.md)) |
| **decide** (`/repacted:decide`) | Work waits on the user | Every open question, numbered, answered in one place |
| **pause** | PR open, not ready to merge | Consolidate memory, persist state, stop the helper agents |

In every workflow a specialist hands in with `repact submit` and does not complete its own task; the lead accepts with `repact accept <id>`, rejects with `repact reject <id> --reason "<text>"` or rules with `repact amend <id> --text "<ruling>"`. The carve-outs are in [Completion Authority](completion.md#completion-authority).

### plan-mode

Planning consultation before implementation: 0 Analyze (scope, select specialists), 1 Consult (specialists in parallel, planning-only mode), 2 Synthesize (resolve conflicts, sequence, assess risk), 3 Present (save `docs/plans/{feature-slug}-plan.md`, await approval). No implementation and no git branch; conflicts are resolved or flagged for the user. After approval the user runs `/repacted:orchestrate {task}`, which references the plan. Recommend comPACT for a trivial task, clarifying questions for unclear requirements, and a PREPARE phase alone when research must come first.

<a id="impact"></a>
### Blocker triage

Trigger: a teammate reports a blocker, similar errors repeat, or a prior phase's output is wrong. Check the agent's state first (converging, exploring or stuck?), then load [blocker-triage.md](../reference/decide/blocker-triage.md) and answer its three questions: redo a prior phase? more agents needed? is the agent recoverable? Its outcomes are redo a prior phase, augment the present phase, a nested cycle, terminate the agent, not truly blocked, or escalate to the user after 3+ triages of one blocker (an ALERT: [stop-signals.md](stop-signals.md)). The task-log writes for a blocker (`blocked`, then `active`) are in [decide.md](../commands/decide.md); `repact decide` lists what still waits on the user.

Conversation Failure Taxonomy (apply after choosing the outcome): Misunderstanding (wrong output, no errors) → teachback correction; Derailment (loops on one error) → fresh agent, new framing; Discontinuity (lost context) → reconstruct from memory and task files; Absence (thin upstream output) → redo the prior phase.

### comPACT

Concurrent specialists for self-contained tasks, single- or cross-domain, with no shared-file dependencies; use orchestrate when phases must chain. Shorthands: `backend`, `frontend`, `database`, `prepare`, `test`, `architect`, `devops`, `security`, `qa` → `pact-<role>`. Auto-select on a clear task; ask the user on an ambiguous one.

- **Parallel is MANDATORY unless tasks share files or have dependencies** ("API endpoint + DB index" = backend + database in parallel), up to the cap on agents at once in the persona's §11 Lean defaults.
- **Pre-invocation:** one `repact dispatch` per specialist (`--branch <branch>` makes the worktree, `--worktree <path>` reuses one); run [Concurrent Dispatch](coordination.md#concurrent-dispatch) when parallel.
- **Specialist instructions:** work from the brief; glance at `docs/plans/`, `docs/preparation/`, `docs/architecture/`; create no new docs; smoke tests only; for parallel or novel work, ask for progress signals in the mission (teammate-messaging.md, Progress Signals).
- **Auditor:** dispatched unless skipped per [Concurrent Audit](coordination.md#concurrent-audit).
- **After completion:** verify the claimed files changed (`git diff --stat`, or the receipt `repact inspect <id>`, never the handoff alone), run the tests (failures go back to the specialist), commit atomically, give the secretary a difficulty assessment when asked, then offer PR (`/repacted:peer-review`), `/repacted:pause`, or more work. A blocker goes to blocker triage; escalate to orchestrate on shared-file sequencing or a need for PREPARE/ARCHITECT.

<a id="variety"></a>
## Complexity

The scoring the lead uses at every dispatch (the four dimensions, the workflow bands, the calibration examples) lives in the orchestrator persona §10. The four scores go to `repact dispatch --variety N,S,U,R` (each 1-4). This section holds Learning II, the records and the stamp detail.

### Learning II: Pattern-Adjusted Scoring

Before finalizing a score, query pact-memory for `"{domain} orchestration_calibration OR review_calibration"` and `"{domain} blocker OR stall OR rePACT"`. If 5+ memories match a recurring pattern (e.g. "auth tasks consistently underestimated"), bump that dimension by 1, note specialist mismatches, and document "Complexity adjusted from {X} to {Y} due to recurring {pattern}". Skip on a project's first session.

### Agent State Model

| State | Indicators | Action |
|---|---|---|
| **Converging** | Progress signals show forward movement | None |
| **Exploring** | Reading, no modifications yet | Normal early; intervene past ~50% of expected duration |
| **Stuck** | No progress signals; stall detection triggers | Send guidance via `SendMessage`; blocker triage if unresponsive |

State transitions: Exploring → Converging is normal; Converging → Exploring is concerning; any → Stuck needs intervention. Ask for progress signals in missions where mid-flight visibility matters (complexity 7+, parallel, novel domains).

<a id="variety-calibration-record"></a>
### Complexity Calibration Record

At workflow completion the secretary gathers the metrics during HANDOFF processing, asks the lead for a difficulty assessment (higher, lower, same) and saves the record to pact-memory with entities `orchestration_calibration` AND `{domain}` (Learning II queries need both). Drift above 2 in any dimension is noted as significant.

```
CalibrationRecord:
  task_id: str                    # feature task id
  domain: str                     # top-level domain, e.g. "auth"
  initial_variety_score: int      # at orchestration start (4-16)
  actual_difficulty_score: int    # post-hoc (4-16)
  dimensions_that_drifted:        # - dimension: novelty|scope|uncertainty|risk; predicted: 1-4; actual: 1-4
  blocker_count: int              # blocker triages
  phase_reruns: int               # phases redone
  specialist_fit: str | null      # good | undermatched | overmatched | null
  timestamp: str                  # ISO 8601
```

<a id="per-dispatch-variety-stamping"></a>
#### The per-dispatch stamp

`repact dispatch --variety N,S,U,R` writes `metadata.variety` on the work task: `{"novelty", "scope", "uncertainty", "risk", "total"}`, each dimension 1-4 and `total` their sum. Score each dispatch afresh, not inherited from or capped by the feature score. The scores give the shape of the work (novelty, breadth, unknowns, exposure), never the expected answer.

- **Teachback mode:** below a total of 10 with the answer `yes`, the program accepts the teachback; at 10+ or on a `concern` the coder stops for the lead (persona §11).
- **Context budget:** the stamp also sets the coder's context lines (`hooks/shared/context_ledger.py`, bytes of estimated context). Default: advise 300 KB, stop 500 KB. Large (`scope` >= 3 or `risk` >= 4): advise 500 KB, stop 800 KB. A `metadata.context_budget_kb` number (clamped to 300..1000) wins over the stamp: it is the stop line, advise is 60% of it. Three notices, once each: advise (to you), stop (to the coder and you: no new work, test and fix what is written, then HANDOFF with `remaining:`), and hard at stop x 1.25 (HANDOFF now). Your own session keeps the default.
- **Journal mirror:** each stamp also writes a `variety_assessed` event for that task with the four scores and total and top-level `"scope": "dispatch"`. Feature-level events carry no `scope`; readers key on that difference.

<a id="variety-acknowledgment-teammate-verification-workflow"></a>
#### The coder's acknowledgment

The coder's teachback answers whether the stamp fits THIS dispatch:

```
"variety_acknowledgment": {"rationale_articulates_this_dispatch": "yes" | "no" | "concern",
                           "concern": "<required when value != 'yes'; names the smell>"}
```

On `no` or `concern` the teachback waits for you ([Teachback Review](completion.md#teachback-review)). A right flag: `repact amend <id>` with the corrected scores and why, then `repact accept <id>`. A wrong flag: put why in `repact amend <id>`, then accept. Never reject a teachback only to disagree about a score. A `concern` that quotes `WITHHELD:` marks a dispatch whose value depends on the coder not knowing something: accept it and leave the stamp. Flags that persist 3+ cycles go to blocker triage.

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

<a id="s4-checkpoints"></a>
### Phase checkpoints

After PREPARE, ARCHITECT and CODE, on unexpected complexity, or when the user asks to pause and assess, review the plan (the milestone, not the minute):

1. **Environment Change**: new requirements, invalidated constraints, changed dependencies?
2. **Model Divergence**: assumptions wrong, estimates off, risks materialized?
3. **Plan Viability**: continue, adapt, or escalate to the user?
4. **Shared Understanding**: does your reading match the specialist's handoff? (At final gates, confirm per [Teachback Review](completion.md#teachback-review); at intermediate ones the next teachback verifies.)

Outcomes: all clear → continue; minor drift → note and continue; significant change → pause, maybe re-run a phase; fundamental shift → ask the user ([stop-signals.md](stop-signals.md#the-rules-that-do-not-bend)). Silent unless an issue appears; then surface `**Checkpoint** [Phase→Phase]: Environment / Model / Plan / Agreement`. An auditor RED prompts a checkpoint.

**Environment model:** for complexity 11+ (recommended 7-10), write `docs/preparation/environment-model-{feature}.md` during PREPARE (stack, external dependencies, constraints, acknowledged unknowns, invalidation triggers) and update it when an assumption breaks; it is the baseline the checkpoint compares against.

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

Decompose at **score ≥ 3** (floor 0); if only one sub-scope emerges, stay single-scope. With neither PREPARE output nor a plan: a Scope score ≥ 3 forces PREPARE; below 3 skips detection. Sub-scopes never re-run detection (the 1-level nesting limit is the safety net); comPACT and a manual nested cycle bypass it.

**Tiers.** Manual (nested cycle, always available). Confirmed (default): ask the user (`📐 Scope Change: Multi-scope task detected`, options A decompose / B single scope / C adjust boundaries); A or C → scope contracts, then ATOMIZE; B → single scope. Autonomous: only when BOTH strong signals fire, no counter-signal, and the injected **PACT Runtime Config** shows `Autonomous scope detection: ON` (`PACT_AUTONOMOUS_SCOPE_DETECTION`); output `Scope detection: Multi-scope (autonomous) — decomposing into [scope list]`.

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

**ATOMIZE.** Create a worktree per scope (`/repacted:worktree-setup`, branch `feature-X--{scope_id}`), persist `TaskUpdate(scopeTaskId, metadata={"scope_contract": {...}, "worktree_path": "...", "nesting_depth": 1})`, and run each scope per [nested-cycle.md](../reference/rePACT/nested-cycle.md), concurrently unless files are shared. The executor reads its inputs from the task file (`TaskGet` does NOT surface metadata: `cat .../tasks/{team_name}/{taskId}.json | jq .metadata.<key>`) and returns the standard HANDOFF plus fulfillment, commits, and `completed` (non-happy paths use `{"stalled": true}` / `{"blocked": true}` metadata). A scope failure is isolated to that scope (blocker triage), but a HALT from any scope stops ALL scopes, preserving their work. Before CONSOLIDATE: every scope done with fulfillment, then a phase checkpoint.

**CONSOLIDATE.** Recover each scope's contract and worktree from its task file (or, if the task store is drained, from the latest `task_metadata_snapshot` in the session journal). Merge each scope branch with `git merge --no-ff` (a conflict is an ALERT: a `shared_files` violation), clean up the worktrees, then in parallel have `pact-architect` verify cross-scope contract compatibility (exports match imports, undelivered items) and `pact-test-engineer` run cross-scope integration tests. Failures route through blocker triage. Commit, then a phase checkpoint.

<a id="s3s4-tension"></a>
## Running the plan or reviewing it

Running the plan asks whether the agents are converging, exploring or stuck; reviewing it asks whether it is still the right thing. Review at task start, on a blocker and at phase ends (plan still viable?); say which you are doing on big decisions.

The two pull against each other: schedule vs quality, execute vs investigate, commit vs adapt, efficiency vs safety. "We're behind, skip PREPARE" is running pushing; "requirements seem unclear" is reviewing pulling. Name the tension, state both paths' gains and risks, weigh them against project values and the user's stated preferences, then decide and document, or ask the user (`⚖️ **Run or review**`, [stop-signals.md](stop-signals.md#the-rules-that-do-not-bend)) when unclear.

## Specialist Autonomy

Specialists may adjust their approach, request context through the lead, recommend scope changes and apply domain expertise without micro-management. They must escalate when a discovery contradicts the architecture, scope changes by more than 20%, security or policy implications appear, or they need a change in another specialist's area. In parallel work they respect assigned boundaries, adopt the first agent's conventions and report conflicts at once.

**Nested PACT:** a specialist may declare "Invoking nested PACT for {sub-task}" for a sub-task with its own uncertainty or several downstream consumers, run a mini P→A→C→T within its domain (skipping unneeded phases), and report it in the handoff. Nesting is limited to 1 level; cross-domain needs go to the lead; stop signals from a nested cycle still go directly to the user. The lead can start it upfront: single domain (`backend "implement rate limiting"`) or multi-domain (`"implement audit logging sub-system"`).
