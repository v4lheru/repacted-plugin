# imPACT: triage detail

> Loaded on demand from [imPACT.md](../../commands/imPACT.md): the S3-vs-algedonic distinction, output examples, the failure taxonomy used to shape a re-dispatch, and the phase re-entry task lifecycle.

## VSM Context: S3 Operational Triage

imPACT is **S3-level triage**—operational problem-solving within normal workflow. It is NOT S5 algedonic escalation (emergency bypass to user).

**imPACT handles**: Blockers that can be resolved by redoing a phase or adding agents.

**Algedonic escalation handles**: Viability threats (security, data, ethics violations). See [algedonic.md](../../protocols/algedonic.md).

**Escalation indicator**: If you run 3+ consecutive imPACT cycles without resolution, this may indicate a systemic issue requiring user intervention (proto-algedonic signal).

**Persist `impact_cycle_count`**: On each imPACT entry, read the feature task file's `metadata.impact_cycle_count` (default 0; `TaskGet` does NOT surface metadata), increment via `TaskUpdate`, check if >= 3 → ALERT (META-BLOCK). Reset to 0 when blocker resolves.

### imPACT vs Algedonic

| Aspect | imPACT | Algedonic |
|--------|--------|-----------|
| **Level** | S3 (operational) | S5 (policy/viability) |
| **Who decides** | Orchestrator triages | User decides |
| **Question** | "How do we proceed?" | "Should we proceed at all?" |
| **Examples** | Missing info, wrong approach, need help | Security breach, data risk, ethics issue |

**When imPACT becomes Algedonic** (uses persisted `impact_cycle_count` from feature task metadata):
- `impact_cycle_count >= 3` without resolution → Emit ALERT (META-BLOCK)
- During imPACT triage, discover viability threat → Emit appropriate HALT/ALERT instead

imPACT is for operational problem-solving. If you're questioning whether the work should continue at all, emit an algedonic signal instead. See [algedonic.md](../../protocols/algedonic.md) for trigger conditions and signal format.

---

## Output Conciseness

See also: [Communication Charter](../../protocols/pact-communication-charter.md) for full plain English, anti-sycophancy, and constructive challenge norms.

**Default: Concise output.** User sees triage outcome, not diagnostic process.

| Internal (don't show) | External (show) |
|----------------------|-----------------|
| Question-by-question analysis | `imPACT: Redo ARCHITECT — interface mismatch` |
| Full diagnostic reasoning | `imPACT: Augmenting with parallel backend coder` |
| Context-gathering details | `imPACT: Not blocked — clarifying guidance to agent` |

**User can always ask** for triage details (e.g., "Why redo that phase?" or "What was the diagnosis?").

| Verbose (avoid) | Concise (prefer) |
|-----------------|------------------|
| "Let me assess whether this is an upstream issue..." | (just do it, report outcome) |
| "The first question is whether we need to redo..." | `imPACT: [outcome] — [reason]` |

---

## Gather Context

Before triaging, quickly check for existing context:
- **Plan**: Check `docs/plans/` for related plan (broader feature context)
- **Prior phase outputs**: Check `docs/preparation/`, `docs/architecture/` for relevant artifacts
- **Progress signals**: If progress monitoring was requested for this agent, review the signal history — was the agent converging, exploring, or stuck before the blocker? This informs the Conversation Failure Taxonomy diagnosis.

This context informs whether the blocker is isolated or systemic.

### Conversation Failure Taxonomy

Use this diagnostic lens **after** identifying an outcome to understand **why** the conversation broke down. Type-specific diagnosis improves the quality of re-dispatch and prevents recurring failures.

| Breakdown Type | Symptoms | Likely Outcome | Recovery Guidance |
|---------------|----------|----------------|-------------------|
| **Misunderstanding** | Wrong output, no errors — agent built the wrong thing | Augment present phase | Teachback correction: clarify what was misunderstood, re-dispatch with corrected context |
| **Derailment** | Loops on same error/approach, unable to make progress | Terminate agent | Fresh agent with different framing — restructure the conversation, don't just retry |
| **Discontinuity** | Lost context, outdated understanding, stale assumptions | Augment present phase | Reconstruct from memory + task files, re-dispatch with restored context |
| **Absence** | Started without sufficient upstream conversation | Redo prior phase | Have the missing conversation first — the upstream phase output was insufficient |

**Usage**: After the three triage questions identify an outcome, check which breakdown type matches. This informs the dispatch prompt for the next agent — e.g., "Prior agent misunderstood the interface contract; the correct contract is X" is more useful than "redo this."

---

## Phase Re-Entry Task Protocol

**Context**: Invoked from [orchestrate.md](../../commands/orchestrate.md) when redoing a prior phase. Complete original blocker first ([Task Operations](../../commands/imPACT.md#task-operations) step 5). Retry failure triggers 3+ cycle escalation (ALERT: META-BLOCK).

**Worktree context**: Phase re-entry operates within the current worktree. The re-entered phase inherits the same worktree path -- no new worktree is created.

When imPACT decides to redo a prior phase (e.g., "redo ARCHITECT because the design was wrong"), follow this Task lifecycle:

1. **Do NOT reopen the old phase task** — it was completed and is historical record
2. **Create a new retry phase task**: `TaskCreate("ARCHITECT (retry): {feature-slug}")`
3. **Set retry task to `in_progress`**
4. **Block the current phase** (the one that hit the blocker): `TaskUpdate(currentPhaseId, addBlockedBy=[retryPhaseId])`
5. **Dispatch agent(s)** for the retry phase
6. **On retry completion**: `TaskUpdate(retryPhaseId, status="completed")` — unblocks the current phase
7. **Retry the current phase** with a new agent task using the updated outputs (re-dispatched agents will teachback their understanding before starting)

