# Coordination: concurrent work, tasks, stalls, completeness, audit

## Concurrent Dispatch

Before invoking agents in parallel, prevent conflicts rather than resolve them:

1. **Identify conflicts:** shared files (merge risk), shared interfaces (contract disagreement), shared state (schemas, config, environment). `TaskList` shows what in-progress agents hold.
2. **Sequence or bound:** sequence the work or assign clear file/component boundaries; with no conflict, dispatch in parallel. Persist `TaskUpdate(codePhaseTaskId, metadata={"s2_boundaries": {"<agent>": ["<paths>"]}})`.
3. **Set resolution authority:** technical disagreements → the architect arbitrates; naming and style → the first agent's choice becomes standard for the batch; resource contention → the lead allocates; a single-instance resource (a Docker daemon, a port) → `repact-lock acquire <name> --owner <you> --wait <seconds>`.

Emit `**S2 Pre-Parallel Check**: shared files / shared interfaces / conventions / anticipated conflicts` when analysing parallel work.

**Conventions.** Extract the first agent's conventions (naming, structure, API style) and put them in later prompts ("Follow conventions established: {list}"). For simultaneous starts, pre-define them, or run one agent first; if agents finish together with no first convention, alphabetical domain order (backend, database, frontend) wins. Persist `metadata.established_conventions` on the CODE phase task. After compaction, read the journal's `s2_state_seeded` event, falling back to the phase task file ([Recovery Steps](recovery.md#recovery-steps)). All parallel agents use the project's terms and the standard handoff structure ([Phase Transitions](workflows.md#phase-transitions)).

**Parallelize by default.** Run QDCL and require a justification for sequential dispatch; 4+ items means multiple agents, no exceptions. "Related" is not "dependent", and "one agent can handle it", "they're small" or "coordination overhead" are rationalizations, not reasons. Valid reasons to sequence: a file modified by both, one agent's output feeding another, or an undefined shared interface (define it first). Guard against over-parallelization with S2 coordination; time-box the analysis to about a minute and default to parallel when unclear. Conflicts are recoverable; lost time is not.

**After each specialist completes:** extract its decisions, conventions and interfaces, and propagate them to later and still-running agents.

### Environment Drift Detection

When dispatching an agent after others have already modified files, check the team's `file-edits.json` (kept by `file_tracker.py`) and include an environment delta in the prompt when files in the new agent's scope changed ("Since your context was set, these files were modified: `src/auth.ts` (by backend-coder). Review before assuming their state."). When a finishing agent touched a still-running agent's scope, `SendMessage` that agent: "Environment changed: {file} was modified by {agent}. Verify your assumptions about it." Skip for single-agent work.

**Backend ↔ database boundary:** the database engineer owns schema, DDL, migrations, complex SQL and indexes, delivered first; the backend engineer owns ORM models, the repository layer, ORM queries and pooling. Each asks the other for the piece it does not own.

## Task Hierarchy

```
Feature task (lead)
├── Phase tasks: PREPARE, ARCHITECT, CODE, TEST (lead)
│   └── Agent tasks (specialists claim; the lead completes)
└── Review task
```

| Transition | Actor | Condition |
|---|---|---|
| `pending → in_progress` (claim) | Teammate | owns the task; `blockedBy` empty |
| `in_progress → in_progress` (metadata) | Teammate | writes `metadata.handoff` / `metadata.teachback_submit` |
| `in_progress → in_progress` (rejection) | Lead | rejection metadata, then the wake |
| `in_progress → completed` | **LEAD ONLY** on teammate-owned tasks | paired with the wake; carve-outs per [Completion Authority](completion.md#completion-authority) |
| `pending → completed` (skip) | Lead | phase skip with `metadata.skipped = true` |

`in_progress` also covers "done, awaiting review": the HANDOFF is stored and the teammate idles on `awaiting_lead_completion`. Every dispatch is a Task A (teachback) + Task B (work) pair, Task A created FIRST, with `blockedBy=[A]` ([Teachback Review](completion.md#teachback-review)). Express dependencies with `addBlockedBy` (CODE blocked by ARCHITECT; agent tasks blocked by their phase). Agent tasks carry `phase`, `domain`, `feature` and the `handoff` in metadata.

**Scoped work:** prefix subjects with `[scope:{scope_id}]` and put `scope_id` in metadata (the Task API cannot filter by scope, so filter on metadata). Scope tasks are created in ATOMIZE; CONSOLIDATE is blocked by every scope task, and TEST by CONSOLIDATE ([Scoped Phases](workflows.md#scoped-phases)).

## Stall Detection

A teammate is stalled when it goes idle, or its process ends, without a completion message or blocker via `SendMessage`, while its task is `in_progress`. Check at every signal point (after dispatch, on TeammateIdle, on `SendMessage` receipt). **Exception:** an idle that crosses a directive you just sent in either order, or a task with a live `intentional_wait`: those idles are delivery-ordering artifacts, not stalls. Read the task file ([Crossed-Wake Idles](completion.md#crossed-wake-idles)); escalate only on task-file-mtime plus sustained-silence. Stuck (from the [Agent State Model](workflows.md#agent-state-model)) is the early warning; a stall is the endpoint.

**Recovery:** read the task status and any partial metadata or message; mark the task `completed` with `{"stalled": true, "reason": "..."}`; decide whether the work can continue; spawn a new teammate with the partial output; if the stall repeats after 1 retry, emit ALERT META-BLOCK. Prevent it by telling agents: "If an error prevents completion, `SendMessage` what you completed and store a partial HANDOFF rather than failing silently."

**Non-happy-path termination** always uses `completed` plus metadata (`{"stalled": true, ...}`, `{"failed": true, ...}` or `{"blocked": true, "blocker_task": "..."}`); no `failed` status exists.

## Completeness Signals

A plan section can exist without being complete. Before skipping a phase on an approved plan (Layer 2 of the 3-layer skip protection in [orchestrate.md](../commands/orchestrate.md), skip reason `"plan_section_complete"`), check its section; **any signal present means the phase runs**. plan-mode uses the same list to fill its Phase Requirements table.

| # | Signal | Look for |
|---|---|---|
| 1 | Unchecked research items | `- [ ]` under "Research Needed" |
| 2 | TBD values | "TBD" (any case) in decision tables |
| 3 | Forward references | the prefix `⚠️ Handled during {PHASE}` (informal "deferred to" also raises suspicion) |
| 4 | Unchecked questions | `- [ ]` under "Questions to Resolve" |
| 5 | Empty or placeholder sections | `{...}` placeholders, or headings with nothing beneath |
| 6 | Unresolved open questions | `- [ ]` under "Open Questions > Require Further Research" |
| 7 | Research tasks in the implementation plan | investigate, research, evaluate, assess feasibility, determine whether, audit, spike, go/no-go |

Checked items (`- [x]`) are resolved.

## Concurrent Audit

The Concurrent Audit Protocol: a `pact-auditor` observes during CODE, complementing TEST and peer review. Cybernetic basis: concurrent observation has more variety than post-hoc review, so drift is caught before it compounds.

### Dispatch Conditions

Dispatched alongside coders by default; to skip, output on its own line: `> **Auditor skipped**: [justification]`. Mandatory, unless the default agent cap (2 at once) is full, at variety ≥ 7, 3+ parallel coders, security-sensitive code (auth, crypto, user input) or a history of architecture drift. Valid skips: a single coder on a familiar pattern, variety reassessed below 7, a user request, or that full cap, where the ONE reviewer's post-artifact audit replaces it.

### Hybrid Observation Model

| Method | When | Cost |
|---|---|---|
| File reading (`git diff`, `Read`) | Every cycle | None |
| `TaskList` monitoring | Coder progress | None |
| `SendMessage` to a coder | Only when files cannot answer; one question per message | Low |
| RED to the lead | A clear architecture violation or requirement misunderstanding | Appropriate |

At least 80% of observation is silent file reading.

### Observation Phases

- **Warm-up:** read the architecture doc, plan and dispatch for key interfaces, high-risk dimensions and coder assignments. Wait for coders to produce initial output before observing. The wake is the orchestrator's stage-ready relay: no hook sends one, so never end the turn with a background process as the route back. Poll `git status --porcelain` on a bounded cadence within the turn; if the turn must end, SET `intentional_wait` (reason `awaiting_coder_output`, resolver `lead`) first and CLEAR it on the relay.
- **Cycles:** diff changed files against architecture spec > approved plan > dispatch. No concern: silent. Minor: note and re-check. Significant but ambiguous: one question to the coder. Clear violation: RED to the lead at once.
- **Final** (on a lead message, or once every coder is complete): sweep all files, emit the summary signal, store `metadata.audit_summary`. RED and YELLOW go out as soon as known; the sweep is never compressed to beat a commit. If the commit lands first, it audits the committed SHA.

### Audit Criteria (Priority Order)

1. Architecture drift (boundaries, interfaces, data flow, dependencies).
2. Risk-proportional attention to high-uncertainty areas.
3. Cross-agent consistency (interfaces, naming, overlap).
4. Cross-cutting gaps (error handling, security basics, performance red flags).
5. Requirement alignment.
6. Decision-log presence, per-PR cycle only: `docs/decision-logs/{feature}-{domain}.md` missing or empty is YELLOW with the expected path (advisory; RED would elicit empty logs).

Not audited: style, coverage, mid-work cleanliness, micro-optimization.

### Structural Verification Discipline

Before GREEN on a **structural** acceptance criterion (a count, line range, path, or touched/untouched/added/removed verb), verify it against diff ground truth: run `git diff <base>..HEAD -- <path>` (or `git show <sha>`), count or locate the artifact in the output (not in the HANDOFF), and cite the command plus hunk or line range so a verifier can reproduce the read. A mismatched count is RED; a matching count in a partly wrong place is YELLOW. File inspection beats HANDOFF inference: four internally-consistent layers of prose can all be wrong together. A judgment-call criterion ("clean naming") is named as such.

**Failure modes to avoid**:
- **PHANTOM-SYMMETRIC-CLAIM**: HANDOFF prose, commit message, coder self-attestation and audit signal all agree on a structural claim. Agreement across layers is cheap; read the diff.
- **VAGUE-DIFF-CITATION**: "see diff" with no path, hunk or line range is not evidence.
- **STRUCTURAL-DRESSING-ON-JUDGMENT-CALL**: a fabricated-looking diff citation on a judgment call.

### Signal Format

```
📋 AUDIT SIGNAL: [GREEN|YELLOW|RED]
Reference: architecture doc / plan / dispatch context
Scope: which coder(s) / files
Finding: one line
Evidence: structural criteria cite `git diff <base>..HEAD -- <path>` plus the hunk or line range; others cite file:line
Action: none (GREEN) / route to test (YELLOW) / intervene (RED)
```

### Signal Levels

GREEN = on track (silence during cycles is implicit GREEN). YELLOW = minor drift or a convention gap (no reference exists → YELLOW, never RED); the lead routes it to TEST. RED = a violation, requirement misunderstanding or security concern; the lead must intervene with the coder. Verify a RED with a targeted question when practical. A viability threat bypasses signals and becomes a full algedonic signal ([Signals](algedonic.md#signals)). References in priority order: `docs/architecture/`, `docs/plans/`, the dispatch, existing codebase conventions.

### Completion Lifecycle

The auditor task has `metadata.completion_type = "signal"`; the auditor stores `metadata.audit_summary` (`{"signal": "GREEN", "findings": [{"level", "scope", "finding"}], "observation_cycles": N, "files_reviewed": N}`) and self-completes.

**Lead side:** the auditor is non-blocking. Absence of `audit_summary` is not a verdict (not GREEN, not RED, not "no findings") nor a promise of one. Before dispatching TEST, confirm a summary exists or an audit against the committed artifact is dispatched; otherwise put the SHA on the auditor's task (`amend_<n>`, kind `commit`) and wake it. Never write a verdict on inferred silence. If you overwrite an auditor verdict, the lifecycle gate preserves the original in `metadata.audit_summary_authored`, moves yours to `metadata.lead_close_note` and emits an `audit_summary_overwrite` advisory (escalated when the verdict is lowered). To read a verdict, prefer `audit_summary_authored` over `audit_summary`.
