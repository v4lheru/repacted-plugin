# /repacted:orchestrate: scoped orchestration and nesting

> Loaded on demand from [orchestrate.md](../../commands/orchestrate.md): load it after PREPARE (scope detection), and when a sub-task outgrows one specialist. PREPARE→ARCHITECT coupling, scope detection, the scoped ATOMIZE/CONSOLIDATE phases, and nested cycles.

### PREPARE→ARCHITECT Coupling

When PREPARE runs, the orchestrator reviews PREPARE output before evaluating ARCHITECT's skip eligibility:

- **PREPARE ran + ARCHITECT hard gates fire** (Scope ≥ 3 or Risk ≥ 3 or total ≥ 10) → Full ARCHITECT, no further analysis needed
- **PREPARE ran + ARCHITECT hard gates don't fire** → Review PREPARE output: *"Did PREPARE reveal new components, interface changes, pattern decisions, or cross-module impact?"* If yes → full ARCHITECT. If no → proceed to structured analysis gate for ARCHITECT.
- **PREPARE skipped** → ARCHITECT evaluated independently through its own gate (no coupling)

**Deferred skip decisions**: When PREPARE runs, do not pre-commit ARCHITECT's skip decision during Context Assessment. Defer until PREPARE results are available, then apply the coupling rules above.

The outcome is binary: full ARCHITECT or skip. No "light ARCHITECT" execution mode.

---

### Scope Detection Evaluation

Evaluate whether the task warrants decomposition into sub-scopes. For heuristic definitions and scoring, see [workflows.md §Scope Detection](../../protocols/workflows.md#scope-detection).

**Precondition**: Requires PREPARE output or plan content as input. If neither exists: Variety Scope >= 3 → Force PREPARE (needs research for reliable detection); Scope < 3 → skip detection (proceed single-scope).

**Process**:
1. Score the task against the heuristics table in the protocol
2. Apply counter-signals to adjust the score downward
3. Determine tier:

| Result | Action |
|--------|--------|
| Score below threshold | Single scope — continue with today's behavior |
| Score at/above threshold | Propose decomposition (see Evaluation Response below) |
| All strong signals fire, no counter-signals, injected `Autonomous scope detection: ON` (PACT Runtime Config) | Auto-decompose (see Evaluation Response below) |

**Output format**: `Scope detection: Single scope (score 2/3 threshold)` or `Scope detection: Multi-scope detected (score 4/3 threshold) — proposing decomposition`

**Persist `scope_detection`**: `TaskUpdate(featureTaskId, metadata={"scope_detection": {"score": N, "threshold": N, "result": "single|multi"}})`

#### Evaluation Response

When detection fires (score >= threshold), follow the evaluation response protocol in [workflows.md §Scope Detection](../../protocols/workflows.md#scope-detection) — S5 confirmation flow, user response mapping, and autonomous tier.

**On confirmed decomposition**: Generate a scope contract for each sub-scope before invoking rePACT. See [workflows.md §Scoped Phases](../../protocols/workflows.md#scoped-phases) for the contract format and generation process. Skip top-level ARCHITECT and CODE — mark both `completed` with `{"skipped": true, "skip_reason": "decomposition_active"}`. The workflow switches to scoped PACT phases: ATOMIZE (dispatch sub-scopes) → CONSOLIDATE (verify contracts) → TEST (comprehensive feature testing). See ATOMIZE Phase and CONSOLIDATE Phase below.

---

#### Handling Complex Sub-Tasks During CODE

If a sub-task emerges that is too complex for a single specialist invocation:

| Sub-Task Complexity | Indicators | Use |
|---------------------|------------|-----|
| **Simple** | Code-only, clear requirements | Direct specialist invocation |
| **Focused** | Independent sub-tasks, no research needed | `/repacted:comPACT` |
| **Complex** | Needs own P→A→C→T cycle | a nested cycle: [nested-cycle.md](../rePACT/nested-cycle.md) |

**When to run a nested cycle:**
- Sub-task needs its own research/preparation phase
- Sub-task requires architectural decisions before coding
- Sub-task spans multiple concerns within a domain

**Phase re-entry** (via `/repacted:imPACT`): When imPACT decides to redo a prior phase, create a new retry phase task — do not reopen the completed one. See [imPACT.md Phase Re-Entry Task Protocol](../../commands/imPACT.md#phase-re-entry-task-protocol) for details.

---

### ATOMIZE Phase (Scoped Orchestration Only)

Execute the [ATOMIZE Phase protocol](../../protocols/workflows.md#scoped-phases).

**Worktree isolation**: Before dispatching each sub-scope's rePACT, invoke `/repacted:worktree-setup` with the suffix branch name (e.g., `feature-X--backend`). Pass the resulting worktree path to the rePACT invocation.

**Persist scope state**: `TaskUpdate(scopeTaskId, metadata={"scope_contract": {...}, "worktree_path": "/path/to/worktree", "nesting_depth": 1})`

---

### CONSOLIDATE Phase (Scoped Orchestration Only)

Execute the [CONSOLIDATE Phase protocol](../../protocols/workflows.md#scoped-phases).

**Worktree cleanup**: After merging each sub-scope branch back to the feature branch, invoke `/repacted:worktree-cleanup` for that sub-scope's worktree.

---

