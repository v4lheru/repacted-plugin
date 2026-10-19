# /repacted:orchestrate: phase-skip gates

> Loaded on demand from [orchestrate.md](../../commands/orchestrate.md): load it before skipping PREPARE, ARCHITECT or TEST. Phases run by default; this is how a skip is earned, recorded, and what coders do about decisions a skipped phase would have made.

## Context Assessment: Phase Skip Decision Flow

Phases run by default. Skipping is an exception that must be earned through a structured gate. The posture is *"can I justify not running this?"* — not *"should I skip?"*

### Three Layers of Skip Protection

All three layers must pass before PREPARE or ARCHITECT can be skipped. Failure at any layer means the phase runs.

| Layer | What It Checks | Fail = |
|-------|----------------|--------|
| **1. Variety Hard Gates** | Dimension scores vs thresholds | Phase locked to "run" — no override possible |
| **2. Plan Completeness** | Approved plan + 7 incompleteness signals (see below) | Phase runs (plan incomplete or absent) |
| **3. Structured Analysis** | Concrete questions requiring specific, verifiable answers | Phase runs (analysis insufficient) |

Layer 1 is a numeric gate — no rationalization possible. Layer 2 is the existing completeness check (unchanged). Layer 3 replaces the old subjective skip criteria.

### Layer 1: Variety Hard Gates

Wire variety dimension scores (already computed in the Task Variety Assessment above) into skip eligibility as non-negotiable gates. These fire before any subjective analysis.

**PREPARE Hard Gates**:

| Dimension | Threshold | Rationale |
|-----------|-----------|-----------|
| **Novelty** ≥ 3 | Phase locked | Unfamiliar territory = unknown unknowns |
| **Uncertainty** ≥ 3 | Phase locked | Unclear requirements = research needed |

**ARCHITECT Hard Gates**:

| Dimension | Threshold | Rationale |
|-----------|-----------|-----------|
| **Scope** ≥ 3 | Phase locked | Cross-cutting change = architectural impact |
| **Risk** ≥ 3 | Phase locked | High stakes = design before coding |

**Global Gate**: If **total variety ≥ 10**, both PREPARE and ARCHITECT are locked to "run" regardless of individual dimensions.

**Threshold rationale**: Dimensions score 1-4. Score 2 = "somewhat familiar/clear" — not alarming. Score 3 = "mostly unfamiliar/unclear" — genuinely warrants the phase. Using ≥ 3 avoids false-locking on routine tasks.

**If any hard gate fires** → Phase runs. No further analysis needed for this phase.

**Missing variety data**: If variety scores are not available, hard gates cannot be evaluated — the default-run posture applies. After compaction, read the journal's `variety_assessed` event first (`python3 "$SJ" read-last --session-dir '{session_dir}' --type variety_assessed --exclude-scope dispatch` — per-dispatch mirrors carry `"scope": "dispatch"` and land later in the journal, so without the exclusion the read returns a dispatch total as the feature assessment); fall back to the feature task file's `metadata.variety` if journal is unavailable (`TaskGet` does NOT surface metadata). See [recovery.md](../../protocols/recovery.md) for the full recovery hierarchy.

### Layer 3: Structured Analysis Gate

When variety hard gates don't lock a phase and no approved plan covers the phase (or the plan section is incomplete), the orchestrator must answer concrete questions to earn a skip. Vague or hedged answers ("probably none", "I think so") mean the phase runs.

**PREPARE — Skip Analysis Questions** (must answer ALL three concretely):

1. **Adjacency check**: *"List all files beyond the direct target(s) that this change could affect."*
   - Must answer with specific file paths or confidently state "none — change is fully isolated to [files]."

2. **Dependency check**: *"What external dependencies, APIs, or constraints exist that aren't stated in the task description?"*
   - Must name them or state "none — all dependencies are documented in the task."

3. **Unknown-unknowns check**: *"What question could PREPARE answer that you can't answer right now?"*
   - Must state "none" with reasoning, not just assertion.

**ARCHITECT — Skip Analysis Questions** (must answer ALL three concretely):

1. **Interface check**: *"Does this change modify or create any interface contract (API, type, schema, protocol) consumed by other components?"*
   - Must answer yes/no with specifics.

2. **Pattern check**: *"What architectural pattern is being followed, and where is it established in the codebase?"*
   - Must cite a specific existing pattern with file references, not just assert "established patterns."

3. **Impact check**: *"Could a reasonable architect disagree with the approach implied by this task?"*
   - If yes, ARCHITECT runs. "No" requires brief reasoning.

**Key property**: These questions require **specific, verifiable answers** (file paths, pattern references, named dependencies) rather than subjective assertions.

**When skipping via structured analysis**: Record the analysis in task metadata for auditability:
`TaskUpdate(phaseTaskId, status="completed", metadata={"skipped": true, "skip_reason": "structured_gate_passed", "skip_analysis": {"adjacency": "...", "dependency": "...", "unknown_unknowns": "..."}})`
`# ARCHITECT example:`
`TaskUpdate(phaseTaskId, status="completed", metadata={"skipped": true, "skip_reason": "structured_gate_passed", "skip_analysis": {"interface": "...", "pattern": "...", "impact": "..."}})`

### Per-Phase Decision Flow

For each of PREPARE and ARCHITECT, evaluate in order:

```
1. Check Layer 1 (variety hard gates) → Locked? → Phase RUNS (no further analysis)
2. Check approved plan + Layer 2 (completeness check) → Plan section complete? → SKIP (reason: "plan_section_complete")
3. Run Layer 3 (structured analysis gate) → All questions answered concretely? → SKIP (reason: "structured_gate_passed")
4. Default → Phase RUNS
```

**CODE phase**: Always runs. Never skip.

**TEST phase**: Skip criteria unchanged — requires ALL four conditions: (1) trivial change with no new logic, (2) no integration boundaries crossed, (3) isolated with no meaningful test scenarios, AND (4) plan doesn't mark TEST as REQUIRED.

**Conflict resolution**: When analysis is ambiguous, phase runs. The burden of proof is on skipping.

**State your assessment briefly.** Example: `Skipping PREPARE (structured gate passed). Running ARCHITECT, CODE, TEST.`

The user can override your assessment or ask for details (e.g., "Show me the skip analysis").

### Phase Skip Completeness Check (Layer 2)

**Principle: Existence ≠ Completeness.** This is Layer 2 of the skip protection (see Per-Phase Decision Flow above). It applies when skipping based on an approved plan.

Before skipping, scan the plan section for incompleteness signals (see [coordination.md §Completeness Signals](../../protocols/coordination.md#completeness-signals)):
- [ ] No unchecked research items (`- [ ]`)
- [ ] No TBD values in decision tables
- [ ] No `⚠️ Handled during {PHASE_NAME}` forward references
- [ ] No unchecked questions to resolve
- [ ] No empty/placeholder sections
- [ ] No unresolved open questions
- [ ] No research/investigation tasks in implementation plan (go/no-go items, feasibility studies, audit tasks)

**All clear** → Skip with reason `"plan_section_complete"`
**Any signal present** → Run the phase

> **Note**: The plan's Phase Requirements table is advisory. When in doubt, verify against actual section content — the table may be stale if the plan was updated after initial synthesis.

**Scope detection**: After PREPARE completes (or is skipped), scope detection evaluates whether the task warrants decomposition into sub-scopes. See [Scope Detection Evaluation](scoped-orchestration.md#scope-detection-evaluation) below.

---

## Handling Decisions When Phases Were Skipped

When a phase is skipped but a coder encounters a decision that would have been handled by that phase:

| Decision Scope | Examples | Action |
|----------------|----------|--------|
| **Minor** | Naming conventions, local file structure, error message wording | Coder decides, documents in commit message |
| **Moderate** | Interface shape within your module, error handling pattern, internal component boundaries | Coder decides and implements, but flags decision with rationale in HANDOFF; orchestrator validates before next phase |
| **Major** | New module needed, cross-module contract, architectural pattern affecting multiple components | Blocker → blocker triage ([blocker-triage.md](../decide/blocker-triage.md)) → may need to run skipped phase |

**Boundary heuristic**: If a decision affects files outside the current specialist's scope, treat it as Major.

**Coder instruction when phases were skipped**:

> "PREPARE and/or ARCHITECT were skipped based on existing context. Minor decisions (naming, local structure) are yours to make. For moderate decisions (interface shape, error patterns), decide and implement but flag the decision with your rationale in the HANDOFF so it can be validated. Major decisions affecting other components are blockers—don't implement, escalate."

---

