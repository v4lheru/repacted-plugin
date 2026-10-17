# /repacted:plan-mode: consultation and synthesis

> Loaded on demand from [plan-mode.md](../../commands/plan-mode.md): load it at Phase 1 for the per-domain questions and the missing-response rules, and at Phase 2 for the synthesis procedure. Also the S4 framing, variety routing, signal handling, output style and the orchestrate hand-off.

## Task Hierarchy

Create a planning Task hierarchy:

```
1. `TaskCreate`: Planning task "Plan: {feature}"
2. `TaskUpdate`: Planning task status = "in_progress"
3. Analyze: Which specialists to consult?
4. `TaskCreate`: Consultation task(s) — one per specialist
5. `TaskUpdate`: Consultation tasks status = "in_progress"
6. `TaskUpdate`: Planning task addBlockedBy = [consultation IDs]
7. Dispatch specialists in parallel (planning-only mode)
8. Monitor until consultations complete
9. `TaskUpdate`: Consultation tasks status = "completed" (as each completes)
10. Synthesize → write plan document
11. `TaskUpdate`: Planning task status = "completed", metadata.artifact = plan path
```

**Example structure:**
```
[Planning] "Plan: user authentication"      (blockedBy: consult1, consult2, consult3)
├── [Consult] "preparer: research auth patterns"
├── [Consult] "architect: design auth service"
└── [Consult] "test-engineer: testing strategy"
```

---

## S4 Intelligence Function

This command is the primary **S4 (Intelligence)** activity in PACT. While `/repacted:orchestrate` operates mainly in S3 mode (execution), `plan-mode` operates entirely in S4 mode:

- **Outside focus**: What does the environment require? What are the constraints?
- **Future focus**: What approach will lead to long-term success?
- **Strategic thinking**: What are the risks? What could change?

The output—an approved plan—bridges S4 intelligence to S3 execution. When `/repacted:orchestrate` runs, it shifts to S3 mode while referencing the S4 plan.

**S4 Questions to Hold Throughout**:
- Are we solving the right problem?
- What could invalidate this approach?
- What are we assuming that might be wrong?
- How might requirements change?

---

## Variety Context

plan-mode is the recommended entry point for **high-variety tasks** (variety score 11-14). It builds understanding before committing to execution.

If variety assessment suggests:
- **Low/Medium variety (4-10)**: Consider recommending `/repacted:comPACT` or direct `/repacted:orchestrate` instead
- **Extreme variety (15-16)**: Consider recommending a research spike first (run PREPARE phase alone)

See [workflows.md §Variety](../../protocols/workflows.md#variety) for the Variety Management assessment protocol.

---

**Domain-specific additions to the template:**

For **📚 pact-preparer**, also ask:
- What documentation/research is needed before implementation?
- What external APIs or libraries need investigation?
- What stakeholder clarifications are needed?

For **🏛️ pact-architect**, also ask:
- What components/modules are affected or needed?
- What design patterns should be applied?
- What interface contracts need definition?

For **coders** (backend/frontend/database/devops), also ask:
- What files need modification or creation?
- What existing patterns in the codebase should be followed?
- What's the implementation sequence?

For **🧪 pact-test-engineer**, also ask:
- What test scenarios are critical (happy path, errors, edge cases)?
- What coverage targets make sense?
- What test data or fixtures are needed?

For **🛡️ pact-security-engineer**, also ask:
- What threat vectors are relevant to this feature?
- What auth/authorization patterns need consideration?
- What input validation or data protection requirements exist?

For **🔍 pact-qa-engineer**, also ask:
- What user flows need runtime verification?
- What pages or interactions are affected?
- What runtime edge cases should be explored (empty states, error states)?

**Handling incomplete or missing responses**:

If a specialist provides minimal, incomplete, or off-topic output:
1. Note the gap — record which specialist and which sections are missing
2. Proceed with synthesis — use the inputs you have
3. Flag in the plan — add to "Limitations" section with specific gaps identified
4. Do NOT re-invoke — avoid infinite loops; missing input is data for the plan

If a specialist fails entirely (timeout, error):
1. Log the failure in synthesis notes
2. Proceed without that perspective
3. Flag prominently in "Open Questions" that this domain was not consulted
4. Recommend the user consider re-running plan-mode or consulting that specialist manually

### Phase 2: Orchestrator Synthesis

After collecting all specialist outputs, use extended thinking to synthesize:

1. **Identify Agreements**
   - Where do specialists align?
   - What's the consensus approach?

2. **Identify and Classify Conflicts**

   Where do specialists disagree? Classify each conflict:

   | Severity | Definition | Action |
   |----------|------------|--------|
   | **Minor** | Different approaches, either works | Orchestrator chooses, documents rationale |
   | **Major** | Fundamental disagreement affecting design | Flag for user decision with options |
   | **Blocking** | Cannot proceed without resolution | Escalate immediately (see below) |

   **Blocking conflict escalation**:
   If a conflict prevents meaningful synthesis (e.g., two specialists propose mutually exclusive architectures):
   1. Stop synthesis
   2. Present partial plan with explicit "BLOCKED" status
   3. Clearly describe the conflict and why it blocks progress
   4. Ask user to resolve before continuing
   5. User may re-run plan-mode after providing direction

3. **Determine Sequencing**
   - What's the optimal order of phases?
   - What can be invoked concurrently?
   - What are the dependencies?

4. **Assess Cross-Cutting Concerns**
   - Security implications?
   - Performance implications?
   - Accessibility implications? (if frontend)
   - Observability/logging needs?

5. **Build Unified Roadmap**
   - Create step-by-step implementation plan
   - Map steps to specialists
   - Identify the commit sequence
   - Populate "Phase Requirements" section based on completeness analysis:
     - Check each phase's plan section for these incompleteness signals:
       - [ ] Unchecked research items (`- [ ]`)
       - [ ] TBD values in decision tables
       - [ ] `⚠️ Handled during {PHASE_NAME}` forward references
       - [ ] Unchecked questions to resolve
       - [ ] Empty/placeholder sections
       - [ ] Unresolved open questions
       - [ ] Research/investigation tasks in implementation plan (go/no-go items, feasibility studies, audit tasks)
     - **Any signal present** → mark phase as REQUIRED
       - For more details on incompleteness signals, see [coordination.md §Completeness Signals](../../protocols/coordination.md#completeness-signals)

6. **Risk Assessment**
   - Aggregate risks from all specialists
   - Assess overall project risk
   - Identify mitigation strategies

7. **Synthesis Validation Checkpoint**

   Before proceeding to Phase 3, verify:
   - [ ] At least 2 specialists contributed meaningful input
   - [ ] No blocking conflicts remain unresolved
   - [ ] All mandatory plan sections can be populated
   - [ ] Cross-cutting concerns have been considered

   If validation fails:
   - For insufficient specialist input → Flag in "Limitations", proceed with available data
   - For unresolved blocking conflict → Present partial plan with BLOCKED status
   - For missing mandatory sections → Populate with "TBD - requires {specific input}"

### Synthesis Agreement Verification

After synthesis passes validation, verify each specialist agrees with the synthesis of their perspective.

**Mechanism**: `SendMessage` to each specialist who contributed to verify:
> "My synthesis of your perspective: [summary of their input as represented in the plan]. Does this accurately represent your input? Did I resolve conflicts involving your domain correctly?"

**Handling responses**:
- **Confirmed**: Proceed to Phase 3
- **Correction provided**: Update synthesis, re-verify with the correcting specialist only. If the correction affects other specialists' sections (e.g., a changed interface), re-verify those specialists too.
- **Specialist unavailable** (shut down): Treat synthesis as accepted for that perspective, note in plan's Limitations section

This verifies the orchestrator's understanding matches what specialists actually recommended — preventing the plan from misrepresenting specialist input. Same agreement verification pattern as orchestrate phase boundaries. Background: [completion.md §Agreement Verification](../../protocols/completion.md).

## Signal Monitoring

During consultation, monitor for blocker and algedonic signals (`SendMessage` from teammates; `TaskList` blocker metadata or stalled status) and handle each as in [orchestrate §Signal Monitoring](../orchestrate/assessment-and-signals.md#signal-monitoring): a signal task, `addBlockedBy` on the agent's task, ALERT blocks the phase task and HALT the feature task, the user resolves, completion unblocks.

---

## Output Conciseness

See also: [Communication Charter](../../protocols/pact-communication-charter.md) for full plain English, anti-sycophancy, and constructive challenge norms.

**Default: Concise output.** User sees plan summary and decision points, not synthesis process.

| Internal (don't show) | External (show) |
|----------------------|-----------------|
| Specialist consultation reasoning | `Consulting: preparer, architect, backend, test` |
| Conflict resolution analysis | Summary in plan document |
| Synthesis process details | `Plan saved to docs/plans/{slug}-plan.md` |

**User can always ask** for details (e.g., "What did the architect recommend?" or "Show me the conflicts").

| Verbose (avoid) | Concise (prefer) |
|-----------------|------------------|
| "The plan has been created. Let me walk you through..." | `Plan saved. Complexity: Medium. 3 decisions need your input.` |
| "I'm now synthesizing the specialist perspectives..." | (just do it, present result) |

---

## When to Recommend Alternative Commands

If during Phase 0 analysis you determine:

- **Task is trivial** (typo, config change, single-line fix) → Recommend `/repacted:comPACT` instead
- **Task is unclear** → Ask clarifying questions before proceeding with planning
- **Task requires immediate research first** → Recommend running preparation phase alone first

---

## Integration with /repacted:orchestrate

After the user approves the plan:

1. User runs `/repacted:orchestrate {same task}`
2. Orchestrator should check for existing plan in `docs/plans/`
3. If plan exists, use it as the implementation specification
4. Specialists receive relevant sections of the plan as context

**Task Linkage**: When `/repacted:orchestrate` runs, it checks for a completed Planning task matching the feature. If found, the plan artifact path from `metadata.artifact` is used to locate and reference the approved plan automatically.

