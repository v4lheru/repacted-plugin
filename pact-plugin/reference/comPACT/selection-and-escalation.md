# /repacted:comPACT: selection, concurrency and escalation detail

> Loaded on demand from [comPACT.md](../../commands/comPACT.md): when the specialist is not named or is ambiguous, when deciding whether items can run concurrently, for output style and signal handling, and when a blocker or a variety surprise suggests escalating.

### If specialist not specified or unrecognized

If the first word isn't a recognized shorthand, treat the entire argument as the task and apply smart selection below.

**Auto-select when clear**:
- Task contains domain-specific keywords:
  - Frontend: React, Vue, UI, CSS, component
  - Backend: Express, API, endpoint, middleware, server
  - Database: PostgreSQL, MySQL, SQL, schema, migration, index
  - Test: Jest, test, spec, coverage
  - Prepare: research, investigate, requirements, explore, compare
  - Architect: pattern, singleton, factory, structure, architecture
  - DevOps: CI/CD, Docker, Dockerfile, pipeline, deploy, infrastructure, Terraform, Makefile, GitHub Actions, workflow, container, Pulumi, CloudFormation
  - Security: vulnerability, CVE, injection, XSS, auth bypass, security audit, penetration, OWASP, secrets, credential
  - QA: runtime, exploratory, browser, Playwright, visual, smoke test, manual test, visual regression, user flow
- Task mentions specific file types (.tsx, .jsx, .sql, .spec.ts, .yml, .yaml, Dockerfile, .sh, .tf, .toml, etc.)
- Proceed immediately: "Delegating to [specialist]..."

**Ask when ambiguous**:
- Generic verbs without domain context (fix, improve, update)
- Feature-level scope that spans domains (login, user profile, dashboard)
- Performance/optimization without specific layer
- → Use `AskUserQuestion` tool:
  - Question: "Which specialist should handle this task?"
  - Options: List the 2-3 most likely specialists based on context (e.g., "Backend" / "Frontend" / "Database")

## When to Invoke Multiple Specialists

**MANDATORY: invoke concurrently unless tasks share files.** The burden of proof is on sequential dispatch.

Invoke concurrently when:
- Multiple independent items (bugs, components, endpoints)
- No shared files between sub-tasks
- Same patterns/conventions apply to all

**Examples:**
| Task | Agents Invoked |
|------|----------------|
| "Fix 3 backend bugs" | 3 backend-coders at once |
| "Add validation to 5 endpoints" | Multiple backend-coders simultaneously |
| "Update styling on 3 components" | Multiple frontend-coders together |
| "Add API endpoint + update DB index" | 1 backend-coder + 1 database-engineer (parallel) |
| "Fix CSS layout + add server logging" | 1 frontend-coder + 1 backend-coder (parallel) |

**Do NOT invoke concurrently when:**
- Sub-tasks modify the same files
- Sub-tasks have dependencies on each other
- Conventions haven't been established yet (run one first to set patterns, then dispatch the rest together)

## Output Conciseness

See also: [Communication Charter](../../protocols/pact-communication-charter.md) for full plain English, anti-sycophancy, and constructive challenge norms.

**Default: Concise output.** User sees delegation decisions, not coordination analysis.

| Internal (don't show) | External (show) |
|----------------------|-----------------|
| S2 coordination analysis, conflict checking | `Delegating to backend coder` |
| Concurrency reasoning, file boundary decisions | `Invoking 3 frontend coders in parallel` |
| Specialist selection logic | `Auto-selected: database (SQL keywords detected)` |

**User can always ask** for details (e.g., "Why that specialist?" or "Show me the conflict analysis").

| Verbose (avoid) | Concise (prefer) |
|-----------------|------------------|
| "Let me check if these sub-tasks share files..." | (just do it, report result) |
| "I'm analyzing whether to invoke concurrently..." | `Concurrent: no shared files` |

## Signal Monitoring

During comPACT, monitor for blocker and algedonic signals (`SendMessage` from teammates; `TaskList` blocker metadata or stalled status) and handle each as in [orchestrate §Signal Monitoring](../orchestrate/assessment-and-signals.md#signal-monitoring): a signal task, `addBlockedBy` on the agent's task, ALERT blocks the phase task and HALT the feature task, the user resolves, completion unblocks.

For agent stall detection and recovery, see [Agent Stall Detection](../../commands/orchestrate.md#agent-stall-detection).

**If blocker reported**:

Examples of blockers:
- Task requires a different specialist's domain
- Missing dependencies, access, or information
- Same error persists after multiple fix attempts
- Scope exceeds self-contained capability (needs PREPARE/ARCHITECT phases)
- Concurrent agents have unresolvable conflicts

When blocker is reported:
1. Receive blocker report from specialist
2. Run `/repacted:imPACT` to triage
3. May escalate to `/repacted:orchestrate` if task exceeds self-contained scope

## When to Escalate

Recommend `/repacted:orchestrate` instead if:
- Sub-tasks have shared-file dependencies requiring sequenced coordination
- Task requires PREPARE or ARCHITECT phases (significant research or design decisions)
- Architectural decisions affect multiple components
- Full preparation/architecture documentation is needed

### Variety-Aware Escalation

During comPACT execution, if you discover the task is more complex than expected:

| Discovery | Variety Signal | Action |
|-----------|----------------|--------|
| Sub-tasks have shared-file dependencies | Medium+ (7+) | Escalate to `/repacted:orchestrate` |
| Significant ambiguity/uncertainty | High (11+) | Escalate; may need PREPARE phase |
| Architectural decisions required | High (11+) | Escalate; need ARCHITECT phase |
| Higher risk than expected | High (11+) | Consider `/repacted:plan-mode` first |

**Heuristic**: If re-assessing variety would now score Medium+ (7+), escalate.

**Conversely**, if the specialist reports the task is simpler than expected:
- Note in HANDOFF to orchestrator
- Complete the task; orchestrator may simplify remaining work

