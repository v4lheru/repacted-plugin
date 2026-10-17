# /repacted:orchestrate: assessment, concurrency and signal detail

> Loaded on demand from [orchestrate.md](../../commands/orchestrate.md): the variety quick-assessment table, S3/S4 mode awareness, output style, the plan-status table, the QDCL dependency check, signal handling and blocker recovery.

## S3/S4 Mode Awareness

This command primarily operates in **S3 mode** (operational control)—executing the plan and coordinating agents. However, mode transitions are important:

| Phase | Primary Mode | Mode Checks |
|-------|--------------|-------------|
| **Before Starting** | S4 | Understand task, assess complexity, check for plans |
| **Context Assessment** | S4 | Should phases be skipped? What's the right approach? |
| **Phase Execution** | S3 | Coordinate agents, track progress, clear blockers |
| **On Blocker** | S4 | Assess before responding—is this operational or strategic? |
| **Between Phases** | S4 | Still on track? Adaptation needed? |
| **After Completion** | S4 | Retrospective—what worked, what didn't? |

When transitioning to S4 mode, pause and ask: "Are we still building the right thing, or should we adapt?"

---

## Responding to Algedonic Signals

For algedonic signal handling (HALT/ALERT responses, algedonic vs imPACT distinction), see [algedonic.md](../../protocols/algedonic.md).

---

## Output Conciseness

See also: [Communication Charter](../../protocols/pact-communication-charter.md) for full plain English, anti-sycophancy, and constructive challenge norms.

**Default: Concise output.** The orchestrator's internal reasoning (variety analysis, dependency checking, execution strategy) runs internally. User sees only decisions and key context.

| Internal (don't show) | External (show) |
|----------------------|-----------------|
| Variety dimension scores, full tables | One-line summary: `Variety: Low (5) — proceeding with orchestrate` |
| QDCL checklist, dependency analysis | Decision only: `Invoking 2 backend coders in parallel` |
| Phase skip reasoning details | Brief: `Skipping PREPARE (structured gate passed)` |

**User can always ask** for details (e.g., "Why that strategy?" or "Show me the variety analysis").

**Narration style**: State decisions, not reasoning process. Minimize commentary.

**Exceptions warranting more detail**:
- Error conditions, blockers, or unexpected issues — proactively explain what went wrong
- High-variety tasks (11+) — visible reasoning helps user track complex orchestration

| Verbose (avoid) | Concise (prefer) |
|-----------------|------------------|
| "Let me assess variety and check for the approved plan" | (just do it, show result) |
| "I'm now going to invoke the backend coder" | `Invoking backend coder` |
| "S4 Mode — Task Assessment" | (implicit, don't announce) |

---

**Quick Assessment Table**:

| If task appears... | Variety Level | Action |
|-------------------|---------------|--------|
| Single file, one domain, routine | Low (4-6) | Offer comPACT using `AskUserQuestion` tool (see below) |
| Multiple files, one domain, familiar | Low-Medium | Proceed with orchestrate (phase gates determine skips) |
| Multiple domains, some ambiguity | Medium (7-10) | Standard orchestrate with all phases |
| Greenfield, architectural decisions, unknowns | High (11-14) | Recommend plan-mode first |
| Novel technology, unclear requirements, critical stakes | Extreme (15-16) | Recommend research spike before planning |

**Variety Dimensions** (score 1-4 each, sum for total):
- **Novelty**: Routine (1) → Unprecedented (4)
- **Scope**: Single concern (1) → Cross-cutting (4)
- **Uncertainty**: Clear (1) → Unknown (4)
- **Risk**: Low impact (1) → Critical (4)

**Output format**: One-line summary only. Example: `Variety: Medium (8) — standard orchestrate with all phases`

### Plan Status Handling

| Status | Action |
|--------|--------|
| PENDING APPROVAL | `/repacted:orchestrate` = implicit approval → update to IN_PROGRESS |
| APPROVED | Update to IN_PROGRESS |
| BLOCKED | Ask user to resolve or proceed without plan |
| IN_PROGRESS | Confirm: continue or restart? |
| SUPERSEDED/IMPLEMENTED | Confirm with user before proceeding |
| No plan found | Proceed—phases will do full discovery |

#### Invoke Concurrently by Default

**Default stance**: Dispatch specialists together unless proven dependent. Sequential requires explicit justification.

**Required decision output** (no exceptions):
- "**Concurrent**: [groupings]" — the expected outcome
- "**Sequential because [specific reason]**: [ordering]" — requires explicit justification
- "**Mixed**: [concurrent groupings], then [sequential dependencies]" — when genuinely mixed

**Deviation from concurrent dispatch requires articulated reasoning.** "I'm not sure" defaults to concurrent with S2 coordination, not sequential.

**Analysis should complete quickly.** Use the Quick Dependency Checklist (QDCL) below. If QDCL analysis takes more than 2 minutes, you're likely over-analyzing independent tasks—default to concurrent dispatch with S2 coordination.

#### Execution Strategy Analysis

**REQUIRED**: Complete the QDCL internally before invoking coders.

**Quick Dependency Checklist (QDCL)** — run mentally, don't output:

For each pair of work units, check:
- Same file modified? → Sequential (or define strict boundaries)
- A's output is B's input? → Sequential (A first)
- Shared interface undefined? → Define interface first, then concurrent
- None of above? → Concurrent

**Output format**: Decision only. Example: `Invoking backend + frontend coders in parallel` or `Sequential: database first, then backend (schema dependency)`

**If QDCL shows no dependencies**: Concurrent is your answer. Don't second-guess.

## Signal Monitoring

Monitor for blocker/algedonic signals via:
- **`SendMessage`**: Teammates send blockers and algedonic signals directly to the team-lead
- **`TaskList`**: Check for tasks with blocker metadata or stalled status
- After each agent dispatch, when agent reports completion, on any unexpected stoppage

On signal detected, handle via the Signal Task Handling procedure:

When an agent reports a blocker or algedonic signal via `SendMessage`:
1. Create a signal Task (blocker or algedonic type)
2. Block the agent's task via `addBlockedBy`
3. For algedonic signals, amplify scope:
   - ALERT → block current phase task (SECURITY or DATA with no exposure now: block only the affected work's merge and any push of its branch)
   - HALT → block feature task (stops all work)
4. Present to user and await resolution
5. On resolution: mark signal task `completed` (unblocks downstream)

**Progress signal assessment**: When progress monitoring was requested, assess incoming progress signals against the agent state model (converging/exploring/stuck) in [workflows.md §Agent State Model](../../protocols/workflows.md#agent-state-model). Intervene if an agent appears stuck or shifts from converging to exploring.

**HALT handling**: On HALT signal, immediately stop all running teammates using the [Lead-Side HALT Fan-Out](../../protocols/algedonic.md#lead-side-halt-fan-out) idiom (one `SendMessage` per in-progress teammate by name) before presenting to user.

### Blocker Recovery: Message the Teammate, or Spawn Fresh

🔴 **The `Agent` tool has no working `resume` parameter. Do not pass one.** A call that carries `resume=` does NOT error. The tool drops the key. A FRESH agent then starts with none of the prior work. The call succeeds and reports an agent, so the call site shows no defect. The defect appears later, as a teammate that knows nothing.

After you resolve a blocker, message the teammate by name. Spawn fresh if the teammate stopped, or if its approach was incorrect.

**Decision matrix**:

| Situation | Action | Rationale |
|-----------|--------|-----------|
| Blocker resolved, teammate is `in_progress` | `amend_<n>` (kind `ruling`) on its task, then the one-line wake | The teammate is live and can continue |
| Blocker resolved, the approach of the teammate was incorrect | Fresh spawn | A clean slate is necessary |
| Teammate hit the `maxTurns` limit | Fresh spawn | The teammate was probably in a loop |
| Teammate shut down for lifecycle cleanup | Fresh spawn | It holds no context |

**Pattern**: `repact-amend next <task id> --kind ruling --text "Blocker resolved: {details}. Continue your task."` prints the key, the object and the wake line. Write the object with `TaskUpdate`, read its stored line (`Amendment amend_<n> stored on #<id> (owner <o>): digest <d>.`), then send the wake line. The teammate reads the ruling with `repact-payload <id> --key amend`.

⚠️ **Write the amendment so that it works alone.** The teammate can hold none of its earlier context. Put the full resolution and what the teammate must do into the amendment text. A self-contained text is correct in the two cases. One that points back at earlier context gives the teammate nothing, and the result looks like a teammate that ignored you.

**Fresh spawn pattern**: Follow the standard dispatch pattern (`TaskCreate` + `TaskUpdate` + Agent with name/team_name/subagent_type/description).

---

