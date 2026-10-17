# Nested PACT cycle (formerly the `rePACT` command)

> Loaded on demand from [orchestrate.md](../../commands/orchestrate.md) when a sub-task needs its own P→A→C→T cycle. The `rePACT` command is retired; this is its procedure, unchanged except that the two `variety_assessed` events are now emitted by the hook. `$ARGUMENTS` below is the sub-task you are nesting.

Run a recursive PACT cycle for this sub-task: $ARGUMENTS

This command initiates a **nested P→A→C→T cycle** for a sub-task that is too complex for simple delegation but should remain part of the current feature work.

**Team behavior**: rePACT spawns sub-scope teammates into the session team (`{team_name}`). The platform provisions the team; rePACT does not create a new one. Use scope-prefixed names (e.g., `backend-coder-auth-scope`) to distinguish sub-scope teammates from parent-scope teammates.

---

## Task Hierarchy

Create a nested Task hierarchy as a child of the current context:

```
1. `TaskCreate`: Sub-feature task "{verb} {sub-feature}" (child of parent context) — stamp metadata.variety.total (4-16)
2. `TaskCreate`: Nested phase tasks:
   - "PREPARE: {sub-feature-slug}"
   - "ARCHITECT: {sub-feature-slug}"
   - "CODE: {sub-feature-slug}"
   - "TEST: {sub-feature-slug}"
3. `TaskUpdate`: Set dependencies:
   - Phase-to-phase blockedBy chain (same as orchestrate)
   - Parent task addBlockedBy = [sub-feature task]
4. `TaskUpdate`: Sub-feature task status = "in_progress"
5. Execute nested P→A→C→T cycle (same per-phase lifecycle as orchestrate: create phase task → `in_progress` → dispatch agents → agent tasks `in_progress` → `completed` → phase `completed`)
6. On completion: Parent task unblocked
```

**Sub-feature journal event**: emitted by the hook from step 1's stamp; do not write it.

**Example structure (standard):**
```
[Feature] "Implement user auth" (parent, blockedBy: sub-feature)
└── [Sub-Feature] "Implement OAuth2 token refresh"
    ├── [Phase] "PREPARE: oauth2-token-refresh"
    ├── [Phase] "ARCHITECT: oauth2-token-refresh"
    ├── [Phase] "CODE: oauth2-token-refresh"
    └── [Phase] "TEST: oauth2-token-refresh"
```

**Scope-aware naming** (when scope contract is provided):

When a scope contract provides a `scope_id`, prefix all tasks with `[scope:{scope_id}]`:

```
[Feature] "Implement user auth" (parent, blockedBy: sub-feature)
└── [Sub-Feature] "[scope:backend-api] Implement backend API"
    ├── [Phase] "[scope:backend-api] PREPARE: backend-api"
    ├── [Phase] "[scope:backend-api] ARCHITECT: backend-api"
    ├── [Phase] "[scope:backend-api] CODE: backend-api"
    └── [Phase] "[scope:backend-api] TEST: backend-api"
```

Include `scope_id` in task metadata: `{ "scope_id": "backend-api" }`. This enables the parent orchestrator to filter tasks by scope when aggregating results.

---

## When to Use rePACT

Run a nested cycle when:
- A sub-task needs full P→A→C→T treatment (prepare, architect, code, test)
- The sub-task should stay on the current branch (no new branch/PR)
- You're already within a `/repacted:orchestrate` workflow

**Don't use rePACT when:**
- Sub-task is simple → use `/repacted:comPACT` instead
- Sub-task is a top-level feature → use `/repacted:orchestrate` instead
- You're not in an active orchestration → use `/repacted:orchestrate` instead

---

## Usage Modes

Below, `nested cycle: [specialist] "<sub-task>"` is shorthand for following this procedure with that sub-task as `$ARGUMENTS`.

### Single-Domain Nested Cycle

When the sub-task fits within one specialist's domain:

```
nested cycle: backend "implement OAuth2 token refresh mechanism"
```

This runs:
1. **Mini-Prepare**: Backend-focused research (token refresh best practices)
2. **Mini-Architect**: Backend component design (token storage, refresh flow)
3. **Mini-Code**: Backend implementation
4. **Mini-Test**: Smoke tests for the sub-component

### Multi-Domain Nested Cycle

When the sub-task spans multiple specialist domains:

```
nested cycle: "implement payment processing sub-system"
```

This runs a mini-orchestration:
1. **Assess scope**: Determine which specialists are needed
2. **Mini-Prepare**: Research across relevant domains
3. **Mini-Architect**: Design the sub-system
4. **Mini-Code**: Invoke relevant coders (may be parallel)
5. **Mini-Test**: Smoke tests for the sub-system

---

## Specialist Selection

| Shorthand | Specialist | Use For |
|-----------|------------|---------|
| `backend` | pact-backend-coder | Server-side sub-components |
| `frontend` | pact-frontend-coder | UI sub-components |
| `database` | pact-database-engineer | Data layer sub-components |
| `prepare` | pact-preparer | Research-only nested cycles |
| `test` | pact-test-engineer | Test infrastructure sub-tasks |
| `architect` | pact-architect | Design-only nested cycles |
| `devops` | pact-devops-engineer | Infrastructure sub-components |
| `security` | pact-security-engineer | Security review nested cycles |
| `qa` | pact-qa-engineer | Runtime verification sub-tasks |

**If no specialist specified**: Assess the sub-task and determine which specialists are needed (multi-domain mode).

---

## Constraints

### Nesting Depth

**Maximum nesting: 1 level**

```
/repacted:orchestrate (level 0)
  └── nested cycle (level 1, max)
        └── nested cycle ← NOT ALLOWED
```

> **Why the limit holds**: scope detection is bypassed within sub-scopes, so a sub-scope cannot trigger further decomposition — level 2 is unreachable rather than merely forbidden.

If you hit the nesting limit:
- Simplify the sub-task and use `/repacted:comPACT`
- Or escalate to user for guidance

---

## Output Conciseness

See also: [Communication Charter](../../protocols/pact-communication-charter.md) for full plain English, anti-sycophancy, and constructive challenge norms.

**Default: Concise output.** User sees nested cycle start/completion, not mini-phase details.

| Internal (don't show) | External (show) |
|----------------------|-----------------|
| Mini-phase transitions | `rePACT: backend "OAuth2 token refresh"` |
| Nesting depth calculations | `rePACT complete. Continuing parent.` |
| Phase skip reasoning | (implicit — just proceed) |

**User can always ask** for nested cycle details (e.g., "What phases ran?" or "Show me the mini-architect output").

| Verbose (avoid) | Concise (prefer) |
|-----------------|------------------|
| "Starting mini-PREPARE phase for the nested cycle..." | (just do it) |
| "The nested cycle has completed successfully..." | `rePACT complete. Continuing parent.` |

**Multi-scope aggregation**: When the parent orchestrator runs multiple rePACT sub-scopes, each sub-scope's HANDOFF feeds into parent-level aggregation. The sub-scope should keep its HANDOFF self-contained (no references to sibling state). The parent orchestrator is responsible for comparing fulfillment sections across sub-scopes during the consolidate phase.

---

### Branch Behavior

Branch behavior depends on whether rePACT is invoked with a scope contract:

**Without scope contract** (standard nested cycle):
- **No new branch**: rePACT stays on the current feature branch
- **No PR**: Results integrate into the parent task's eventual PR
- All commits remain part of the current feature work

**With scope contract** (from ATOMIZE phase):
- **Receives worktree path** from the parent orchestrator (created by parent via `/repacted:worktree-setup`)
- **Operates in isolated worktree** on a suffix branch (e.g., `feature-X--{scope_id}`)
- **Pass worktree path to all agent prompts**: Include "You are working in a git worktree at [worktree_path]. All file paths must be absolute and within this worktree. As a teammate, do NOT write a `CLAUDE.md` file in a project directory or a home directory. This covers each route to that write, not only an `Edit` or a `Write` you issue: if a script you run, a command you invoke, or a save path you trigger writes the file, that write is yours. This applies with or without a worktree. The orchestrator manages those files. If your task mentions updating `CLAUDE.md`, flag it in your HANDOFF instead." in specialist dispatches, consistent with orchestrate.md
- All commits stay on the suffix branch within the worktree
- Branch merges back to the feature branch during the CONSOLIDATE phase

---

## Workflow

### Phase 0: Assess

Before starting, verify:
1. **Nesting depth**: Read the task file's `metadata.nesting_depth` (`TaskGet` does NOT surface metadata) — if > 1, reject (max depth exceeded). If absent, treat as 0.
2. **Scope contract**: If this rePACT was dispatched from ATOMIZE, read the scope contract from the task file's `metadata.scope_contract` instead of expecting it inline in the prompt. This ensures scope state survives compaction.
   - **Journal fallback (covers items 1–2)**: If the task file cannot be read (task store drained), recover from the session journal instead: run `python3 "{plugin_root}/hooks/shared/session_journal.py" read --session-dir "{session_dir}" --type task_metadata_snapshot` (prints a JSON array), filter to events whose `task_id` matches this task, take the latest-`ts` event, and read `nesting_depth` / `scope_contract` from its `metadata` (honor `_truncated` / `_dropped_keys` markers if present).
3. **Scope appropriateness**: Is this truly a sub-task of the parent?
4. **Domain determination**: Single-domain or multi-domain?

### Phase 1: Mini-Prepare (if needed)

For the sub-task, gather focused context:
- Research specific to the sub-component
- May be skipped if parent Prepare phase covered this
- Output: Notes integrated into parent preparation or separate `-nested` doc

### Phase 2: Mini-Architect (if needed)

Design the sub-component:
- Component design within the larger architecture
- Interface contracts with parent components
- May be skipped for simple sub-tasks
- Output: Design notes in `-nested` architecture doc or inline

### Phase 3: Mini-Code

Implement the sub-component:

**Session team**: the nested cycle dispatches into `{team_name}` too, the team the platform provisioned; never create one.

Dispatch each specialist exactly as [orchestrate.md's Teachback-Gated Dispatch](../../commands/orchestrate.md#teachback-gated-dispatch) (Task A then Task B, the variety stamp on Task B, the canonical `Agent(...)` spawn), with two nested-cycle differences: the teammate name is scope-prefixed (`{scope-prefixed-name}`, e.g. `backend-coder-auth-scope`), and the subjects are `"{scope-prefixed-name}: TEACHBACK for {sub-task}"` and `"{scope-prefixed-name}: implement {sub-task}"`.

For multi-domain: spawn multiple specialists in parallel.
Apply S2 coordination if parallel work.
Output: Code + HANDOFF in task metadata (summary via `SendMessage` to team-lead).

### Phase 4: Mini-Test

Verify the sub-component:
- Smoke tests for the sub-component
- Verify integration with parent components
- Output: Test results in HANDOFF

### Phase 5: Integration

Complete the nested cycle:
1. **Verify**: Sub-component works within parent context
2. **HANDOFF**: Return control to parent orchestration with summary
3. **Agreement verification**: Parent orchestrator verifies understanding of nested results before reintegrating into parent scope.
   - `SendMessage` to each contributing specialist to confirm: "Confirming my understanding of the nested results: [restates key deliverables and decisions]. Correct?"
   - Background: [completion.md §Agreement Verification](../../protocols/completion.md).

---

## Context Inheritance

Nested cycles inherit from parent:
- Current feature branch
- Parent task context and requirements
- Architectural decisions from parent
- Coding conventions established in parent

Nested cycles produce:
- Code committed to current branch
- Handoff summary for parent orchestration

**CT note**: Nested cycles are *conversations within conversations*. The parent scope's understanding provides context for the nested conversation. When the nested conversation completes, its understanding must be verified before reintegrating into the parent. Agreement verification at nested boundaries follows the same mechanism as phase boundaries in orchestrate. Background: [completion.md §Agreement Verification](../../protocols/completion.md).

---

## Scope Contract Reception

When the parent orchestrator invokes rePACT with a **scope contract** (from scope detection and decomposition), the nested cycle operates scope-aware. Without a contract, rePACT behaves as described above. Contract presence is the mode switch — there are no explicit "modes" to select.

**When a scope contract is provided:**

1. **Identity**: Use the contract's `scope_id` as the scope identifier for all task naming and metadata (see Task Hierarchy above)
2. **Deliverables**: Treat contracted deliverables as the success criteria for Mini-Code and Mini-Test
3. **Interfaces**: Use `imports` to understand what sibling scopes provide; use `exports` to ensure this scope exposes what siblings expect
4. **Shared files constraint**: Do NOT modify files listed in the contract's `shared_files` — these are owned by sibling scopes. Communicate this constraint to all dispatched specialists.
5. **Conventions**: Apply any `conventions` from the contract in addition to inherited parent conventions
6. **HANDOFF**: Include a Contract Fulfillment section in the completion HANDOFF (see After Completion below)

**When no scope contract is provided:** Standard rePACT behavior. No scope-aware naming, no contract fulfillment tracking, no shared file constraints.

See [workflows.md §Scoped Phases](../../protocols/workflows.md#scoped-phases) for the contract format specification.

---

## Relationship to Specialist Autonomy

Specialists can invoke nested cycles autonomously (see [Autonomy Charter](../../protocols/workflows.md#specialist-autonomy)).
This procedure is for **orchestrator-initiated** nested cycles.

| Initiator | Mechanism |
|-----------|-----------|
| Specialist discovers complexity | Uses Autonomy Charter (declares, executes, reports) |
| Orchestrator identifies complex sub-task | Follows this procedure |

The two share the P→A→C→T *shape*, not this document's task mechanics. A specialist's nested cycle is a mini-cycle inside its own work, reported through its HANDOFF — the Autonomy Charter defines no `TaskCreate`, no `TaskUpdate`, and no dispatch wiring, so do NOT create or wire Task A / Task B pairs from a specialist. Creating a nested team and wiring dispatches is the orchestrator's path, via this procedure.

If a specialist's sub-task genuinely needs its own dispatched teammates, that is a cross-domain escalation to the orchestrator, not a nested cycle to run in place.

---

## Examples

### Example 1: Single-Domain Backend Sub-Task

```
nested cycle: backend "implement rate limiting middleware"
```

Orchestrator runs mini-cycle:
- Mini-Prepare: Research rate limiting patterns
- Mini-Architect: Design middleware structure
- Mini-Code: Invoke backend coder
- Mini-Test: Smoke test rate limiting

### Example 2: Multi-Domain Sub-System

```
nested cycle: "implement audit logging sub-system"
```

Orchestrator assesses scope:
- Needs: backend (logging service), database (audit tables), frontend (audit viewer)
- Runs mini-orchestration with all three domains
- Coordinates via S2 protocols

### Example 3: Skipping Phases

```
nested cycle: frontend "implement form validation component"
```

If parent already has:
- Validation requirements (skip mini-prepare)
- Component design (skip mini-architect)

Then just run mini-code and mini-test.

---

## Error Handling

**If nesting limit exceeded:**
```
⚠️ NESTING LIMIT: Cannot invoke rePACT at level 2.
Options:
1. Simplify sub-task and use comPACT
2. Escalate to user for guidance
```

**If sub-task is actually top-level:**
```
⚠️ SCOPE MISMATCH: This appears to be a top-level feature, not a sub-task.
Consider using /repacted:orchestrate instead.
```

---

## Signal Monitoring

In the nested cycle, monitor for blocker and algedonic signals (`SendMessage` from teammates; `TaskList` blocker metadata or stalled status) and handle each as in [orchestrate §Signal Monitoring](../orchestrate/assessment-and-signals.md#signal-monitoring): a signal task, `addBlockedBy` on the agent's task, ALERT blocks the phase task and HALT the feature task, the user resolves, completion unblocks.

For agent stall detection and recovery, see [Agent Stall Detection](../../commands/orchestrate.md#agent-stall-detection).

---

## After Completion

When nested cycle completes:
1. **`TaskUpdate`**: Sub-feature task status = "completed"
2. **Summarize** what was done in the nested cycle
3. **Report** any decisions that affect the parent task
4. **Continue** with parent orchestration (parent task now unblocked)

**Handoff format**: Use the standard handoff structure (Produced, Key decisions, Reasoning chain [recommended], Areas of uncertainty, Integration points, Open questions — 5 required fields, 1 recommended). These are PROSE labels. When you store this same HANDOFF in task metadata, use the canonical `metadata.handoff` keys, in this order: `produced`, `decisions`, `reasoning_chain`, `uncertainty`, `integration`, `open_questions`. Never convert a display label into a key — `Key decisions` is the label, `decisions` is the key.

**Contract-aware HANDOFF** (when scope contract was provided): Append a Contract Fulfillment section after the standard HANDOFF:

```
Contract Fulfillment:
  Deliverables:
    - ✅ {delivered item} → {actual file/artifact}
    - ❌ {undelivered item} → {reason}
  Interfaces:
    exports: {what was actually exposed}
    imports: {what was actually consumed from siblings}
  Deviations: {any departures from the contract, with rationale}
```

The parent orchestrator uses fulfillment sections from all sub-scopes to drive the consolidate phase. Keep the fulfillment section factual and concise — the parent only needs to know what matched, what didn't, and why.

The parent orchestration resumes with the sub-task complete.
